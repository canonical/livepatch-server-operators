# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Action to hash basic auth users for the `auth.basic.users` config option."""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Tuple

import bcrypt
import ops
from ops.charm import ActionEvent

from config import KEY_AUTH_BASIC_ENABLED, KEY_AUTH_BASIC_USERS, SECRET_FALLBACK_OPTIONS, SECRET_VALUE_KEY

if TYPE_CHECKING:
    from charm import OperatorMachineCharm

# The key under which the `users` value is stored in the `users-secret` content.
SECRET_USERS_KEY = "users"  # nosec B105


def _find_duplicate(existing_users: str, set_users: List[str]) -> Optional[str]:
    """Find the first user that is already configured.

    Args:
        existing_users: comma-separated `username:hash` pairs.
        set_users: the usernames the action is about to add.

    Returns:
        The duplicated username, or None if there is no overlap.
    """
    for existing_user in existing_users.split(","):
        username = existing_user.split(":")[0]
        if username in set_users:
            return username
    return None


def _hash_users(users_list: List[str]) -> Tuple[List[str], List[str]]:
    """Hash the passwords of the requested users.

    Args:
        users_list: `username:password` pairs supplied to the action.

    Returns:
        A tuple of the `username:hash` pairs, and the usernames on their own.

    Raises:
        ValueError: if an entry is malformed, a username or password is
            empty, a username is duplicated in the same call, or a password
            exceeds the 72 byte bcrypt limit.
    """
    hashed_users: List[str] = []
    set_users: List[str] = []
    for index, user in enumerate(users_list, start=1):
        # maxsplit=1 lets a password contain further colons; only the first
        # one separates it from the username.
        parts = user.split(":", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid entry #{index}; expected the form `username:password`.")
        username, password = parts
        if not username:
            raise ValueError(f"Invalid entry #{index}; the username cannot be empty.")
        if not password:
            # Deliberately not naming the password: an empty bcrypt hash is a
            # valid hash, and a blank password would let anyone authenticate
            # as this user.
            raise ValueError(f"The password for {username} cannot be empty.")
        # The current version of bcrypt truncates passwords over 72 bytes, and a
        # multi-byte UTF-8 character can push the byte length past the
        # character length, so the encoded form is what must be checked.
        # https://github.com/pyca/bcrypt#maximum-password-length
        encoded_password = password.encode("utf-8")
        if len(encoded_password) > 72:
            # The password itself is deliberately kept out of the message, as
            # it would otherwise reach the unit status and the Juju log.
            raise ValueError(f"The password for {username} cannot be more than 72 bytes when utf-8 encoded.")
        if username in set_users:
            raise ValueError(f"The user {username} is listed more than once.")
        hashed = bcrypt.hashpw(encoded_password, bcrypt.gensalt(rounds=10))
        hashed_users.append(f"{username}:{hashed.decode()}")
        set_users.append(username)
    return hashed_users, set_users


def _resolve_users(self: OperatorMachineCharm, event: ActionEvent) -> Optional[str]:
    """Resolve the `users` value from whichever of the two params was given.

    Args:
        self: the charm the action is running on.
        event: the action event, holding the `users` and `users-secret` params.

    Returns:
        The `username:password` pairs to hash, or None if the action should
        stop (a failure has already been recorded on the event).
    """
    users: Optional[str] = event.params.get("users")
    secret_id: Optional[str] = event.params.get("users-secret")

    if users and secret_id:
        event.fail("Provide either `users` or `users-secret`, not both.")
        return None
    if not users and not secret_id:
        event.fail("No users provided. Provide either `users` or `users-secret`.")
        return None

    if secret_id:
        try:
            secret = self.model.get_secret(id=secret_id)
            content = secret.get_content(refresh=True)
        except ops.SecretNotFoundError:
            event.fail(
                "Could not access the secret. Run `juju grant-secret <secret> " f"{self.app.name}` and try again."
            )
            return None
        users = content.get(SECRET_USERS_KEY)
        if not users:
            event.fail(f"The secret must have a `{SECRET_USERS_KEY}` key with the same format as the `users` param.")
            return None
        return users

    event.log(
        "WARNING: `users` is stored in plaintext in Juju's action history "
        "(visible via `juju show-task`/`juju show-operation`). Prefer "
        "`users-secret` outside of local testing."
    )
    return users


def on_set_basic_users_action(self: OperatorMachineCharm, event: ActionEvent) -> None:
    """Hash a list of "user:password" pairs for use with the `auth.basic.users` config option.

    This action does not change the server's configuration. It bcrypt-hashes
    the given passwords and returns the resulting values.
    It also displays the commands needed to apply the hashed values, preferably
    via the `auth.basic.users-secret` Juju secret URI option, or via the
    deprecated plaintext `auth.basic.users` option.

    Set `append=true` to combine the new users with the ones already present
    in `auth.basic.users`/`auth.basic.users-secret`, instead of replacing them.
    """
    event.log("Hashing basic auth users...")

    users = _resolve_users(self, event)
    if users is None:
        return

    try:
        hashed_users, set_users = _hash_users(users.split(","))
    except ValueError as e:
        event.fail(str(e))
        return

    existing_secret_id = self.config.get(SECRET_FALLBACK_OPTIONS[KEY_AUTH_BASIC_USERS])
    if event.params.get("append") is True:
        if existing_secret_id:
            try:
                existing_users = self.model.get_secret(id=existing_secret_id).get_content(refresh=True).get(
                    SECRET_VALUE_KEY
                )
            except ops.SecretNotFoundError:
                event.fail(
                    "Could not access the secret configured for `auth.basic.users-secret`. Run "
                    f"`juju grant-secret <secret> {self.app.name}` and try again."
                )
                return
            if existing_users is None:
                event.fail(
                    "The secret configured for `auth.basic.users-secret` must have a "
                    f"`{SECRET_VALUE_KEY}` key."
                )
                return
        else:
            existing_users = self.config.get(KEY_AUTH_BASIC_USERS)
        if not existing_users:
            event.fail(
                "No existing users configured to append to; set `auth.basic.users` or "
                "`auth.basic.users-secret` first."
            )
            return
        duplicate = _find_duplicate(existing_users, set_users)
        if duplicate:
            event.fail(f"The user {duplicate} already exists.")
            return
        combined_users = f"{existing_users},{','.join(hashed_users)}"
        if existing_secret_id:
            command = (
                f"juju update-secret {existing_secret_id} {SECRET_VALUE_KEY}='{combined_users}' && "
                f"juju config {self.app.name} {KEY_AUTH_BASIC_ENABLED}=true"
            )
        else:
            command = (
                f"juju config {self.app.name} {KEY_AUTH_BASIC_USERS}='{combined_users}' "
                f"{KEY_AUTH_BASIC_ENABLED}=true"
            )
    else:
        combined_users = ",".join(hashed_users)
        command = (
            f"juju add-secret livepatch-basic-users {SECRET_VALUE_KEY}='{combined_users}' && "
            f"juju grant-secret livepatch-basic-users {self.app.name} && "
            f"juju config {self.app.name} {SECRET_FALLBACK_OPTIONS[KEY_AUTH_BASIC_USERS]}=<secret-uri-from-add-secret> "
            f"{KEY_AUTH_BASIC_ENABLED}=true"
        )

    event.log("Apply the hashed users by running:")
    event.log(command)
    event.set_results(
        {
            KEY_AUTH_BASIC_USERS: combined_users,
            "command": command,
        }
    )
