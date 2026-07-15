"""SaaS Sandbox Container Lifecycle Manager.
"""

from __future__ import annotations

import logging
import time
import uuid
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class ContainerManager:
    """Manages lifecycle of Docker sandbox containers for SaaS mode."""

    MAX_CONTAINERS = 50
    IDLE_TIMEOUT_SECONDS = 900
    STARTUP_TIMEOUT_SECONDS = 30

    def __init__(self, image_name: str = "yanyu-wit-sandbox:latest"):
        self.image_name = image_name
        self._containers: Dict[str, Dict[str, Any]] = {}  # user_id -> {container_id, last_active}
        self._reaper_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    async def initialize(self):
        """Start background reaper and pre-pull sandbox image."""
        async with self._lock:
            if not self._reaper_task:
                self._reaper_task = asyncio.create_task(self._reap_idle_loop())
                logger.info("ContainerManager reaper task started")

        # Pre-pull sandbox image in background to reduce first-request cold start
        from service.feature_flags import get_flags
        if get_flags().sandbox_type == "docker":
            asyncio.create_task(self._prepull_image())

    async def _prepull_image(self):
        """Pre-pull the sandbox Docker image to eliminate image-pull latency on first request."""
        loop = asyncio.get_running_loop()
        def _pull():
            try:
                import docker
                client = docker.from_env()
                try:
                    client.images.get(self.image_name)
                    logger.info("Sandbox image '%s' already cached locally", self.image_name)
                except docker.errors.ImageNotFound:
                    logger.info("Pre-pulling sandbox image '%s'...", self.image_name)
                    client.images.pull(self.image_name)
                    logger.info("Successfully pre-pulled sandbox image '%s'", self.image_name)
            except Exception as e:
                logger.warning("Failed to pre-pull sandbox image: %s", e)
        await loop.run_in_executor(None, _pull)

    async def get_or_create(self, user_id: str, workspace_path: str) -> str:
        """Get existing or spin up a new Docker sandbox container for the user.

        Args:
            user_id: Unique user identifier.
            workspace_path: Host physical path to user's workspace.
        """
        async with self._lock:
            # 1. Check if already active
            if user_id in self._containers:
                info = self._containers[user_id]
                container_id = info["container_id"]
                # Verify container is still running
                if await self._is_container_running(container_id):
                    info["last_active"] = time.time()
                    return container_id
                else:
                    # Clean stale entry
                    logger.warning(f"Container {container_id} for user {user_id} was stopped unexpectedly. Recreating.")
                    self._containers.pop(user_id, None)

            # 2. Enforce MAX_CONTAINERS limit by reaping oldest idle container
            if len(self._containers) >= self.MAX_CONTAINERS:
                await self._reap_one_oldest_unlocked()

            # 3. Create and start a new container
            container_id = await self._start_sandbox(user_id, workspace_path)
            self._containers[user_id] = {
                "container_id": container_id,
                "last_active": time.time(),
            }
            return container_id

    async def release(self, container_id: str):
        """Update last active time of the container."""
        async with self._lock:
            for user_id, info in self._containers.items():
                if info["container_id"] == container_id:
                    info["last_active"] = time.time()
                    break

    async def stop_all(self):
        """Stop and remove all managed containers on shutdown."""
        async with self._lock:
            if self._reaper_task:
                self._reaper_task.cancel()
                self._reaper_task = None

            import docker
            try:
                client = docker.from_env()
            except Exception as e:
                logger.error(f"Failed to connect to docker daemon on shutdown: {e}")
                return

            for user_id, info in list(self._containers.items()):
                container_id = info["container_id"]
                logger.info(f"Stopping container {container_id} for user {user_id} on shutdown")
                try:
                    container = client.containers.get(container_id)
                    container.stop(timeout=2)
                    container.remove(force=True)
                except Exception as e:
                    logger.warning(f"Failed to stop/remove container {container_id}: {e}")
            self._containers.clear()

    async def _is_container_running(self, container_id: str) -> bool:
        import docker
        try:
            client = docker.from_env()
            container = client.containers.get(container_id)
            return container.status == "running"
        except Exception:
            return False

    async def _start_sandbox(self, user_id: str, workspace_path: str) -> str:
        """Create and start container via docker SDK."""
        import docker
        loop = asyncio.get_running_loop()
        
        def run():
            client = docker.from_env()
            
            # Check if sandbox image exists. If not, use python:3.12-alpine as fallback
            image = self.image_name
            try:
                client.images.get(image)
            except docker.errors.ImageNotFound:
                fallback = "python:3.12-alpine"
                logger.warning(f"Image {image} not found. Falling back to {fallback}")
                image = fallback
                # Pre-pull fallback if not exists
                try:
                    client.images.get(image)
                except docker.errors.ImageNotFound:
                    logger.info(f"Pulling fallback image {image}...")
                    client.images.pull(image)

            # Generate unique container name
            container_name = f"yanyu-wit-sandbox-{user_id}-{uuid.uuid4().hex[:8]}"
            
            # Configuration
            volumes = {
                str(Path(workspace_path).resolve()): {
                    "bind": "/workspace",
                    "mode": "rw"
                }
            }
            tmpfs = {"/tmp": ""}
            
            labels = {
                "yanyu-wit-user": user_id,
                "yanyu-wit-role": "sandbox"
            }

            logger.info(f"Starting sandbox container for user {user_id} with image {image}")
            container = client.containers.run(
                image=image,
                command="tail -f /dev/null",  # Keeps container alive
                name=container_name,
                detach=True,
                read_only=True,
                volumes=volumes,
                tmpfs=tmpfs,
                network_mode="none",
                mem_limit="512m",
                nano_cpus=1000000000,  # 1.0 CPU limit
                labels=labels,
                user="root",  # We run container as root, but exec runs command as non-privileged "agent"
            )
            
            # Set up non-privileged agent user inside Alpine if needed,
            # or ensure a safe user is available.
            # In Alpine python image, uid 1000 is usually not created by default.
            try:
                # Add user "agent" with UID 1000 if not exists
                container.exec_run("adduser -D -u 1000 agent", user="root")
                # Ensure /workspace and /tmp is owned by agent (or writeable by agent)
                container.exec_run("chown -R agent:agent /workspace /tmp", user="root")
            except Exception as ex:
                logger.warning(f"Failed to setup non-privileged user inside container: {ex}")

            return container.id

        return await loop.run_in_executor(None, run)

    async def _reap_one_oldest_unlocked(self):
        """Reap the oldest container to make space."""
        if not self._containers:
            return
        
        oldest_user = min(self._containers.keys(), key=lambda k: self._containers[k]["last_active"])
        info = self._containers.pop(oldest_user)
        container_id = info["container_id"]
        logger.info(f"Reaping oldest container {container_id} for user {oldest_user} due to capacity limit")
        
        import docker
        def run():
            try:
                client = docker.from_env()
                container = client.containers.get(container_id)
                container.stop(timeout=2)
                container.remove(force=True)
            except Exception as e:
                logger.warning(f"Failed to reap container {container_id}: {e}")

        await asyncio.get_running_loop().run_in_executor(None, run)

    async def _reap_idle_loop(self):
        """Periodically reap idle containers."""
        while True:
            try:
                await asyncio.sleep(60)
                async with self._lock:
                    now = time.time()
                    for user_id, info in list(self._containers.items()):
                        if now - info["last_active"] > self.IDLE_TIMEOUT_SECONDS:
                            self._containers.pop(user_id)
                            container_id = info["container_id"]
                            logger.info(f"Reaping idle container {container_id} for user {user_id}")
                            
                            import docker
                            def run():
                                try:
                                    client = docker.from_env()
                                    container = client.containers.get(container_id)
                                    container.stop(timeout=2)
                                    container.remove(force=True)
                                except Exception as e:
                                    logger.warning(f"Failed to reap idle container {container_id}: {e}")
                            
                            asyncio.create_task(asyncio.get_running_loop().run_in_executor(None, run))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in container reaper loop: {e}")
