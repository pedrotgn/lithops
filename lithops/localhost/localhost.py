#
# (C) Copyright Cloudlab URV 2021
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import os
import json
import uuid
import shlex
import time
import signal
import lithops
import logging
import shutil
import xmlrpc.client
import subprocess as sp
from shutil import copyfile
from pathlib import Path

from lithops import utils
from lithops.version import __version__
from lithops.constants import TEMP_DIR, USER_TEMP_DIR, \
    LITHOPS_TEMP_DIR, COMPUTE_CLI_MSG, SV_LOG_FILE
from lithops.utils import is_lithops_worker, is_unix_system

logger = logging.getLogger(__name__)

SERVICE_FILE = os.path.join(LITHOPS_TEMP_DIR, 'localhost-service.py')
LITHOPS_LOCATION = os.path.dirname(os.path.abspath(lithops.__file__))


class LocalhostHandler:
    """
    A localhostHandler object is used by invokers and other components to
    access underlying localhost backend without exposing the implementation
    details.
    """

    def __init__(self, localhost_config):
        logger.debug('Creating Localhost compute client')
        self.config = localhost_config
        self.runtime_name = self.config['runtime']
        self.env = None

        msg = COMPUTE_CLI_MSG.format('Localhost compute')
        logger.info(f"{msg}")

    def get_backend_type(self):
        """
        Wrapper method that returns the type of the backend (Batch or FaaS)
        """
        return 'batch'

    def init(self):
        """
        Init tasks for localhost
        """
        self.env = DefaultEnv(self.config) if '/' not in self.runtime_name else DockerEnv(self.config)
        self.env.setup()
        self.env.start_service()

    def deploy_runtime(self, runtime_name, *args):
        """
        Extract the runtime metadata and preinstalled modules
        """
        logger.info(f"Deploying runtime: {runtime_name}")
        return self.env.get_metadata()

    def invoke(self, job_payload):
        """
        Run the job description against the selected environment
        """
        executor_id = job_payload['executor_id']
        job_id = job_payload['job_id']
        total_calls = len(job_payload['call_ids'])

        logger.debug(f'ExecutorID {executor_id} | JobID {job_id} - Running '
                     f'{total_calls} activations in the localhost worker')

        self.env.run(job_payload)

    def get_runtime_key(self, runtime_name, *args):
        """
        Generate the runtime key that identifies the runtime
        """
        env_type = 'default' if '/' not in runtime_name else 'docker'
        runtime_key = os.path.join('localhost', __version__, env_type, runtime_name.strip("/"))

        return runtime_key

    def get_runtime_info(self):
        """
        Method that returns a dictionary with all the relevant runtime
        information set in config
        """
        runtime_info = {
            'runtime_name': self.config['runtime'],
            'runtime_memory': self.config.get('runtime_memory'),
            'runtime_timeout': self.config.get('runtime_timeout'),
            'max_workers': self.config['max_workers'],
        }

        return runtime_info

    def clean(self, **kwargs):
        pass

    def clear(self, job_keys=None):
        """
        Kills the localhost service
        """
        self.env.stop()


class BaseEnv:
    """
    Base environment class for shared methods
    """

    def __init__(self, config):
        self.config = config
        self.runtime_name = self.config['runtime']
        self.worker_processes = self.config['worker_processes']
        self.service_process = None
        self.client = None

    def _copy_lithops_to_tmp(self):
        if is_lithops_worker() and os.path.isfile(SERVICE_FILE):
            return
        os.makedirs(LITHOPS_TEMP_DIR, exist_ok=True)
        shutil.rmtree(os.path.join(LITHOPS_TEMP_DIR, 'lithops'), ignore_errors=True)
        shutil.copytree(LITHOPS_LOCATION, os.path.join(LITHOPS_TEMP_DIR, 'lithops'))
        src_handler = os.path.join(LITHOPS_LOCATION, 'localhost', 'service.py')
        copyfile(src_handler, SERVICE_FILE)

    def get_metadata(self):
        logger.debug(f"Extracting runtime metadata from: {self.runtime_name}")

        while True:
            try:
                response = self.client.extract_runtime_meta()
                break
            except ConnectionRefusedError:
                time.sleep(1)

        if response:
            return json.loads(response)
        else:
            raise Exception("An error ocurred trying to get the runtime metadata. "
                            f"Check the {SERVICE_FILE.replace('.py', '.log')}")

    def run(self, job_payload):
        """
        Adds a job to the localhost service
        """
        invoked = False
        while not invoked:
            try:
                invoked = self.client.add_job(json.dumps(job_payload))
            except ConnectionRefusedError:
                time.sleep(1)

    def start_service(self):
        raise NotImplementedError

    def stop(self):
        """
        Stops localhost service
        """
        if self.service_process.poll() is None:
            PID = self.service_process.pid
            logger.debug(f'Stopping localhost service with PID {PID}')
            if is_unix_system():
                PGID = os.getpgid(PID)
                os.killpg(PGID, signal.SIGKILL)
            else:
                os.kill(PID, signal.SIGTERM)


class DefaultEnv(BaseEnv):
    """
    Default environment uses current python3 installation
    """

    def __init__(self, config):
        super().__init__(config)
        logger.debug(f'Starting Default Environment for {self.runtime_name}')

    def setup(self):
        logger.debug('Setting up Default python environment')
        self._copy_lithops_to_tmp()

    def start_service(self):
        if self.service_process and self.service_process.poll() is None:
            # The service is running
            return

        if not os.path.isfile(SERVICE_FILE):
            self.setup()

        logger.debug('Starting localhost worker service - Python environment')

        service_port = utils.find_free_port()

        cmd = [self.runtime_name, SERVICE_FILE, str(self.worker_processes), str(service_port)]
        log = open(SV_LOG_FILE, 'a')
        process = sp.Popen(cmd, stdout=log, stderr=log, start_new_session=True)
        self.service_process = process

        self.client = xmlrpc.client.ServerProxy(f'http://localhost:{service_port}')


class DockerEnv(BaseEnv):
    """
    Docker environment uses a docker runtime image
    """

    def __init__(self, config):
        super().__init__(config)
        logger.debug(f'Starting Docker Environment for {self.runtime_name}')
        self.container_id = str(uuid.uuid4()).replace('-', '')[:12]
        self.uid = os.getuid() if is_unix_system() else None
        self.gid = os.getuid() if is_unix_system() else None

    def setup(self):
        logger.debug('Setting up Docker environment')
        self._copy_lithops_to_tmp()
        if self.config.get('pull_runtime', False):
            logger.debug('Pulling Docker runtime {}'.format(self.runtime_name))
            sp.run(shlex.split(f'docker pull {self.runtime_name}'), check=True,
                   stdout=sp.PIPE, universal_newlines=True)

    def start_service(self):
        if self.service_process and self.service_process.poll() is None:
            # The service is running
            return

        if not os.path.isfile(SERVICE_FILE):
            self.setup()

        logger.debug('Starting localhost worker service - Docker environemnt')

        gpu = self.config.get('gpu', False)

        tmp_path = Path(TEMP_DIR).as_posix()
        docker_path = utils.get_docker_path()
        logger.info("getting port")
        service_port = utils.find_free_port()
        logger.info(service_port)

        cmd = f'{docker_path} run --name lithops_{self.container_id} '
        cmd += '--gpus all ' if gpu else ''
        cmd += f'--user {self.uid}:{self.gid} ' if is_unix_system() else ''
        cmd += f'--env USER={os.getenv("USER", "root")} '
        cmd += f'--rm -v {tmp_path}:/tmp --entrypoint "python3" '
        cmd += f'{self.runtime_name} /tmp/{USER_TEMP_DIR}/localhost-service.py '
        cmd += f'{str(self.worker_processes)} {str(service_port)}'

        log = open(SV_LOG_FILE, 'a')
        process = sp.Popen(shlex.split(cmd), stdout=log, stderr=log, start_new_session=True)
        self.service_process = process

        self.client = xmlrpc.client.ServerProxy(f'http://localhost:{service_port}')

    def stop(self, job_keys=None):
        """
        Stops localhost service container containers
        """
        sp.Popen(shlex.split(f'docker rm -f lithops_{self.container_id}'),
                 stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        super().stop(job_keys)
