# coding: utf-8
import os
from logging import getLogger

from dotenv import load_dotenv
from yaml import safe_load as yaml_load
from toml import load as toml_load
from json import load as json_load, loads as json_loads, JSONDecodeError

import utils as u
from models import ConfigModel, env_vaildate_json_keys
from pydantic import ValidationError

l = getLogger(__name__)


class Config:
    '''
    用户配置
    '''

    config: ConfigModel

    def __init__(self):
        perf = u.perf_counter()  # 性能计数器

        # ===== prepare .env =====
        # v5 stores environment configuration in data/.env.  When upgrading
        # the old DGLab-Sleepy fork, transparently fall back to its root .env.
        data_env = u.get_path('data/.env')
        legacy_env = u.get_path('.env', create_dirs=False)
        load_dotenv(dotenv_path=data_env if os.path.exists(data_env) else legacy_env)
        config_env = {}
        legacy_env_aliases = {
            'secret': 'main_secret',
            'main_https_enabled': 'main_https',
            'page_user': 'page_name',
            'page_repo': 'page_learn_more_link',
            'page_learn_more': 'page_learn_more_text',
            'page_sorted': 'status_sorted',
            'page_using_first': 'status_using_first',
            'util_metrics': 'metrics_enabled',
            'turnstile_enabled': 'plugin_dglab_turnstile_enabled',
            'turnstile_site_key': 'plugin_dglab_turnstile_site_key',
            'turnstile_secret_key': 'plugin_dglab_turnstile_secret_key',
        }
        try:
            # 筛选有效配置项
            vaild_kvs: dict[str, str] = {}
            for k_, v in os.environ.items():
                k = k_.lower()
                if k.startswith('sleepy_'):
                    key = k[7:]
                    vaild_kvs[legacy_env_aliases.get(key, key)] = v
            # 生成字典
            for k, v in vaild_kvs.items():
                if k in env_vaildate_json_keys:
                    try:
                        v = json_loads(v)
                    except JSONDecodeError:
                        pass
                klst = k.split('_')
                config_env = u.deep_merge_dict(config_env, u.process_env_split(klst, v))
        except Exception as e:
            l.warning(f'Error when loading environment variables: {e}')

        # ===== prepare config.yaml =====
        config_yaml = {}
        try:
            if os.path.exists(u.get_path('data/config.yaml')):
                with open(u.get_path('data/config.yaml'), 'r', encoding='utf-8') as f:
                    config_yaml = yaml_load(f)
                    f.close()
        except Exception as e:
            l.warning(f'Error when loading data/config.yaml: {e}')

        # ===== prepare config.toml =====
        config_toml = {}
        try:
            if os.path.exists(u.get_path('data/config.toml')):
                with open(u.get_path('data/config.toml'), 'r', encoding='utf-8') as f:
                    config_toml = toml_load(f)
                    f.close()
        except Exception as e:
            l.warning(f'Error when loading data/config.toml: {e}')

        # ===== prepare config.json =====
        config_json = {}
        try:
            if os.path.exists(u.get_path('data/config.json')):
                with open(u.get_path('data/config.json'), 'r', encoding='utf-8') as f:
                    config_json = json_load(f)
                    f.close()
        except Exception as e:
            l.warning(f'Error when loading data/config.json: {e}')

        # ===== mix sources =====
        try:
            self.config = ConfigModel(**u.deep_merge_dict(config_env, config_yaml, config_toml, config_json))
        except ValidationError as e:
            raise u.SleepyException(f'Invaild config!\n{e}')

        # ===== optimize =====
        # status_list 中自动补全 id
        for i in range(len(self.config.status.status_list)):
            self.config.status.status_list[i].id = i

        # metrics_list 中 [static] 处理
        if '[static]' in self.config.metrics.allow_list:
            self.config.metrics.allow_list.remove('[static]')
            static_list = u.list_dirs(u.get_path('static/'))
            self.config.metrics.allow_list.extend(['/static/' + i for i in static_list])

        if self.config.main.debug:
            # *此处还未设置日志等级, 需手动判断*
            l.debug(f'[config] init took {perf()}ms')
