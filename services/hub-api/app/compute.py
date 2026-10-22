"""Compute executor layer: create/start/stop/status/connection.

`DockerExecutor` runs a resource-limited CPU container with sshd on this host.
`MockExecutor` is used for GPU specs (clearly flagged as simulated) and for
local development when Docker is unavailable.  A future AutoDL adapter only
needs to implement the same methods.
"""
import os
import secrets
import shutil
import subprocess

SPECS = {
    "cpu-small": {"label": "CPU 1核 / 2GiB", "cpus": "1", "memory": "2g", "gpu": False},
    "cpu-medium": {"label": "CPU 2核 / 4GiB", "cpus": "2", "memory": "4g", "gpu": False},
    "gpu-4090": {"label": "RTX 4090 24GB（模拟）", "gpu": True},
    "gpu-a100": {"label": "A100 80GB（模拟）", "gpu": True},
}
IMAGES = {
    "python": os.environ.get("HUB_CPU_IMAGE", "elite-hub/cpu-python:latest"),
}
SSH_HOST = os.environ.get("HUB_SSH_HOST", "1.14.48.23")
PORT_RANGE = range(int(os.environ.get("HUB_SSH_PORT_MIN", "22100")), int(os.environ.get("HUB_SSH_PORT_MAX", "22120")))


class MockExecutor:
    simulated = True

    def create(self, inst_id, spec, image, used_ports):
        port = next(p for p in PORT_RANGE if p not in used_ports)
        return {"container_id": f"mock-{inst_id}", "ssh_host": "simulated.local", "ssh_port": port,
                "ssh_user": "root", "ssh_password": secrets.token_urlsafe(9)}

    def stop(self, container_id):
        pass


class DockerExecutor:
    simulated = False

    def create(self, inst_id, spec, image, used_ports):
        s = SPECS[spec]
        port = next(p for p in PORT_RANGE if p not in used_ports)
        pw = secrets.token_urlsafe(12)
        name = f"elite-hub-inst-{inst_id}"
        out = subprocess.run([
            "docker", "run", "-d", "--name", name,
            "--cpus", s["cpus"], "--memory", s["memory"], "--pids-limit", "256",
            "--label", "elite-hub=1", "-p", f"{port}:22",
            "-e", f"ROOT_PASSWORD={pw}", IMAGES[image],
        ], capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip()[:500])
        return {"container_id": out.stdout.strip(), "ssh_host": SSH_HOST, "ssh_port": port,
                "ssh_user": "root", "ssh_password": pw}

    def stop(self, container_id):
        subprocess.run(["docker", "rm", "-f", container_id], capture_output=True, timeout=60)


def executor_for(spec: str):
    if SPECS[spec]["gpu"] or os.environ.get("HUB_COMPUTE_MODE", "docker") == "mock" or not shutil.which("docker"):
        return MockExecutor()
    return DockerExecutor()
