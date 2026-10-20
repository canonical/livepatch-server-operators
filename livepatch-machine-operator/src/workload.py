# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Snap workload management for the Livepatch machine charm.

This module owns every interaction with the ``canonical-livepatch-server`` snap,
including the change detection that keeps the reconciler idempotent. Each
mutating method reports whether it actually changed anything, so the caller can
decide whether a restart is warranted instead of restarting on every hook.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from charms.operator_libs_linux.v2.snap import (
    Snap,
    SnapAPIError,
    SnapCache,
    SnapError,
    SnapNotFoundError,
    SnapState,
)

from config import SNAP_CONFIG_PREFIX
from constants.snap import SERVER_SNAP_NAME, SERVER_SNAP_REVISION

logger = logging.getLogger(__name__)

LIVEPATCH_SERVICE = "livepatch"


def _flatten(config: Any, prefix: str) -> Dict[str, str]:
    """Flatten the nested mapping returned by ``snap get -d`` into dotted keys.

    Args:
        config: the nested configuration value to flatten.
        prefix: the dotted key prefix accumulated so far.

    Returns:
        A mapping of dotted snap configuration keys to their string values.
    """
    if not isinstance(config, dict):
        return {prefix: _normalise(config)}

    flattened: Dict[str, str] = {}
    for key, value in config.items():
        flattened.update(_flatten(value, f"{prefix}.{key}"))
    return flattened


def _normalise(value: Any) -> str:
    """Render a configuration value the way the snap stores it.

    Configuration is written with untyped ``snap set``, which stringifies every
    value. Comparing string renderings on both sides of the diff therefore
    avoids spurious mismatches between, say, ``True`` and ``"True"``.

    Args:
        value: the value to render.

    Returns:
        The string rendering of the value.
    """
    return "" if value is None else str(value)


class SnapWorkload:
    """The Livepatch server snap, and everything the charm does to it."""

    def __init__(
        self,
        snap_name: str = SERVER_SNAP_NAME,
        desired_revision: Any = SERVER_SNAP_REVISION,
    ) -> None:
        """Construct.

        Args:
            snap_name: name of the snap to manage.
            desired_revision: the revision this charm revision pins the snap to.
        """
        self._snap_name = snap_name
        self._desired_revision = str(desired_revision)
        self._cache: Optional[SnapCache] = None

    @property
    def snap(self) -> Snap:
        """The underlying snap object.

        Returns:
            The snap from the (lazily built) snap cache.

        Raises:
            SnapError: if the snap cannot be looked up, for any reason.
        """
        if self._cache is None:
            self._cache = SnapCache()
        try:
            return self._cache.get(self._snap_name)
        except (SnapNotFoundError, SnapAPIError) as e:
            # Neither is a subclass of SnapError, but callers only guard against
            # that one, so every snap lookup failure is normalised to it here.
            raise SnapError(str(e)) from e

    def _invalidate_cache(self) -> None:
        """Discard the cached snap data so the next read comes from snapd.

        ``Snap.ensure`` does not update the cached revision, and more than one
        reconcile can run in a single hook, so the cache must be dropped after
        any install or refresh.
        """
        self._cache = None

    @property
    def present(self) -> bool:
        """Whether the snap is installed.

        Returns:
            True if the snap is present on the machine.
        """
        return self.snap.present

    @property
    def installed_revision(self) -> str:
        """The revision currently installed.

        Returns:
            The installed revision, as a string.
        """
        return str(self.snap.revision)

    @property
    def desired_revision(self) -> str:
        """The revision this charm pins the snap to.

        Returns:
            The pinned revision, as a string.
        """
        return self._desired_revision

    @property
    def running(self) -> bool:
        """Whether the livepatch daemon is active.

        Returns:
            True if the snap is installed and its daemon is running.
        """
        try:
            service = self.snap.services.get(LIVEPATCH_SERVICE)
        except SnapError:
            logger.warning("could not read snap services")
            return False
        return bool(service and service.get("active"))

    def ensure_revision(self) -> bool:
        """Install or refresh the snap so it matches the pinned revision.

        The snap is pinned to a revision baked into the charm, and held, so
        snapd never auto-refreshes it. The revision therefore only moves when
        the charm itself is upgraded.

        The hold itself is never read back, because doing so costs a snap store
        round trip. An unhold only matters once it lets an auto-refresh
        through, which shows up here as a revision mismatch and is corrected.

        Returns:
            True if the snap was installed or refreshed.
        """
        if not self.present:
            logger.info("installing %s revision %s", self._snap_name, self._desired_revision)
            self.snap.ensure(SnapState.Present, revision=self._desired_revision)
            self._invalidate_cache()
            self.snap.hold()
            return True

        if self.installed_revision != self._desired_revision:
            logger.info(
                "refreshing %s from revision %s to %s",
                self._snap_name,
                self.installed_revision,
                self._desired_revision,
            )
            # A hold does not block a refresh request naming this snap, which
            # is what `ensure` issues, so it never has to be lifted. It is
            # re-applied below in case the refresh cleared it.
            self.snap.ensure(SnapState.Present, revision=self._desired_revision)
            self._invalidate_cache()
            self.snap.hold()
            return True

        return False

    def current_config(self) -> Dict[str, str]:
        """Read the charm-managed snap configuration.

        Returns:
            A flat mapping of dotted snap configuration keys to string values.
            Empty if the snap has no configuration set yet.
        """
        try:
            raw = self.snap.get(SNAP_CONFIG_PREFIX, typed=True)
        except SnapError:
            # snapd errors when the option tree has never been written.
            logger.debug("no existing `%s` snap configuration", SNAP_CONFIG_PREFIX)
            return {}
        if raw is None:
            return {}
        return _flatten(raw, SNAP_CONFIG_PREFIX)

    def config_delta(self, desired: Dict[str, Any]) -> Dict[str, Any]:
        """Compute which desired configuration keys differ from the snap.

        Keys present in the snap but absent from ``desired`` are ignored: the
        charm has never unset stale keys, and treating them as a difference
        would make the reconciler restart the workload on every hook.

        Args:
            desired: the desired flat snap configuration.

        Returns:
            The subset of ``desired`` whose values differ from the snap.
        """
        current = self.current_config()
        # A snap option set to an empty string is removed by snapd, so an
        # absent key and an empty desired value are equivalent.
        return {key: _normalise(value) for key, value in desired.items() if current.get(key, "") != _normalise(value)}

    def apply_config(self, desired: Dict[str, Any]) -> bool:
        """Write the desired configuration to the snap, if it differs.

        Args:
            desired: the desired flat snap configuration.

        Returns:
            True if any configuration value was written.
        """
        delta = self.config_delta(desired)
        if not delta:
            return False
        logger.info("updating %d snap configuration value(s)", len(delta))
        self.snap.set(delta)
        return True

    def restart(self) -> None:
        """Restart the livepatch daemon."""
        logger.info("restarting the livepatch daemon")
        self.snap.restart([LIVEPATCH_SERVICE])

    def stop(self) -> None:
        """Stop the livepatch daemon, ignoring failures during teardown."""
        try:
            self.snap.stop([LIVEPATCH_SERVICE])
        except SnapError as e:
            logger.warning("could not stop the livepatch daemon: %s", e)
