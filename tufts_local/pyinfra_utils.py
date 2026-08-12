from pathlib import Path
from unittest import result
from venv import logger
from pyinfra.api import Config, Inventory, State, deploy
from pyinfra.api.connect import connect_all, disconnect_all
from pyinfra.api.operations import run_ops
from pyinfra.operations import files, python
from pyinfra.api.deploy import add_deploy
from pyinfra.api.exceptions import PyinfraError


@deploy("Create personal scratch directory")
def create_personal_scratch_directory(username: str = None):
    scratch_dir = Path('/cluster/scratch') / username
    try:
        r = files.directory(
            name="Create personal scratch directory",
            path=scratch_dir.as_posix(),
            user="root",
            group=f"{username}_g",
            mode="770",
        )
        def success_callback():
            print(f"Got result: {r.stdout}")
    
        python.call(
            name="Execute callback function",
            function=success_callback,
        )
    except PyinfraError as e:
        print(f"Error creating personal scratch directory: {e}")
        def error_callback():
            print(r.stderr)

        python.call(
            name="Execute error callback function",
            function=error_callback,
        )


def run_deployments(deployments, hosts, ssh_user=None, ssh_key=None) -> list:
    override_data = {}
    if ssh_user:
        override_data['ssh_user'] = ssh_user
    if ssh_key:
        override_data['ssh_key'] = ssh_key

    state = State(inventory=Inventory(hosts, override_data=override_data),
                  config=Config(SUDO=True))
    try:
        connect_all(state)
        for deployment in deployments:
            add_deploy(state, deployment[0], **deployment[1])

        run_ops(state)
        return state
    except PyinfraError as e:
        print(f"Error running deployments: {e}")
        raise e
    finally:
        disconnect_all(state)
