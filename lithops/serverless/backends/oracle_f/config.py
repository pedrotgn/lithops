# (C) Copyright Cloudlab URV 2023
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
from lithops.constants import TEMP_DIR


DEFAULT_CONFIG_KEYS = {
    'runtime_timeout': 300,  # Default: 5 minutes
    'runtime_memory': 256,  # Default memory: 256 MB
    'max_workers': 300,
    'worker_processes': 1,
    'invoke_pool_threads': 64,
}

CONNECTION_POOL_SIZE = 300

APPLICATION_NAME = 'lithops'
BUILD_DIR = os.path.join(TEMP_DIR, 'OracleRuntimeBuild')

REQUIREMENTS_FILE = """
    oci
    pika
    tblib
    cloudpickle
    ps-mem
"""

AVAILABLE_PY_RUNTIMES = ['3.6', '3.7', '3.8', '3.9']


REQ_PARAMS = ('tenancy', 'user', 'fingerprint', 'key_file', 'region')

def load_config(config_data=None):
    if 'oracle' not in config_data:
        raise Exception("'oracle' section is mandatory in the configuration")

    for param in REQ_PARAMS:
        if param not in config_data['oracle']:
            msg = f'"{param}" is mandatory in the "oci" section of the configuration'
            raise Exception(msg)

    for key in DEFAULT_CONFIG_KEYS:
        if key not in config_data['oracle_f']:
            config_data['oracle_f'][key] = DEFAULT_CONFIG_KEYS[key]

    if 'vcn' not in config_data['oracle_f'] or 'subnet_ids' not in config_data['oracle_f']['vcn']:
        raise Exception("'vcn' and 'subnet_ids' are mandatory in the 'oracle_f' section of the configuration")
    else:
        config_data['oracle_f']['subnet_ids'] = config_data['oracle_f']['vcn']['subnet_ids']

    config_data['oracle_f'].update(config_data['oracle'])