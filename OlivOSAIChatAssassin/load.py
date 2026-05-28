import os
import json
import threading
from enum import Enum
from typing import Dict, Optional

import OlivOSAIChatAssassin


def load_logger(Proc):
    OlivOSAIChatAssassin.data.gProc = Proc


def load_init_after():
    OlivOSAIChatAssassin.data.gMessageHistory = {}
    OlivOSAIChatAssassin.data.gData = OlivOSAIChatAssassin.load.DataManager(
        config_dir=OlivOSAIChatAssassin.data.gConfigDir,
        memory_dir=OlivOSAIChatAssassin.data.gMemoryDir,
        default_config=OlivOSAIChatAssassin.data.configDefault
    )


def load_bot(bot_hash_list: str):
    pass
    if OlivOSAIChatAssassin.data.gData is None:
        OlivOSAIChatAssassin.logger.warn('加载数据失败')
    else:
        for bot_hash in bot_hash_list:
            OlivOSAIChatAssassin.data.gData.load_bot(bot_hash)


def write_memory(bot_hash: str):
    if OlivOSAIChatAssassin.data.gData is None:
        OlivOSAIChatAssassin.logger.warn('写入记忆失败')
    else:
        OlivOSAIChatAssassin.data.gData.save_bot_memory(bot_hash)


def load_staticKnowledge():
    OlivOSAIChatAssassin.data.gStaticKnowledge = {}
    try:
        os.makedirs(OlivOSAIChatAssassin.data.gStaticKnowledgeDir, exist_ok=True)
        for i in os.listdir(OlivOSAIChatAssassin.data.gStaticKnowledgeDir):
            f_name = f'{OlivOSAIChatAssassin.data.gStaticKnowledgeDir}/{i}'
            try:
                with open(f_name, 'r', encoding='utf-8') as f:
                    f_obj = json.loads(f.read())
                    if type(f_obj) is not dict:
                        OlivOSAIChatAssassin.logger.warn(f'加载知识库[{i}]失败: 类型错误[{type(f_obj)}]')
                    else:
                        OlivOSAIChatAssassin.data.gStaticKnowledge.update(**f_obj)
                        OlivOSAIChatAssassin.logger.log(f'已加载知识库[{i}]')
            except Exception as e:
                OlivOSAIChatAssassin.logger.warn(f'加载知识库[{i}]失败: {e}')
        OlivOSAIChatAssassin.logger.log(f'已加载知识库共[{len(OlivOSAIChatAssassin.data.gStaticKnowledge)}]条')
    except Exception as e:
        OlivOSAIChatAssassin.logger.warn(f'加载知识库完全失败: {e}')


class DataManagerType(Enum):
    CONFIG = 'CONFIG'
    MEMORY = 'MEMORY'


class DataManager:
    def __init__(self, config_dir: str, memory_dir: str, default_config: dict):
        """
        :param config_dir:    存放配置文件的目录（对应 gConfigDir）
        :param memory_dir:    存放记忆文件的目录（对应 gMemoryDir）
        :param default_config: 默认配置字典（对应 gMemoryDefault 或 configDefault）
        """
        self.config_dir = config_dir
        self.memory_dir = memory_dir
        self.default_config = default_config

        # botHash -> 配置字典
        self.config: Dict[str, dict] = {}
        # botHash -> 记忆字典
        self.memory: Dict[str, dict] = {}

        # 用于 get 方法的分发映射
        self._data_mapping = {
            DataManagerType.CONFIG: self.config,
            DataManagerType.MEMORY: self.memory,
        }

        # 记录文件修改时间，实现热重载（可根据需要扩展）
        self._config_mtime: Dict[str, float] = {}
        self._memory_mtime: Dict[str, float] = {}

        # 读写锁
        self._config_lock = threading.Lock()
        self._memory_lock = OlivOSAIChatAssassin.data.gMemoryLock

    # ==================== 核心加载逻辑 ====================

    def _load_bot_config(self, bot_hash: str) -> dict:
        """
        加载指定 bot 的配置，支持回退：
        1. 优先使用 config_{botHash}.json
        2. 不存在则使用 config.json
        3. 仍不存在则使用默认配置并写入 config_{botHash}.json
        """
        flag_need_read = True
        flag_need_write = False

        os.makedirs(self.config_dir, exist_ok=True)

        config = {}
        specific_path = os.path.join(self.config_dir, f"config_{bot_hash}.json")
        common_path = os.path.join(self.config_dir, "config.json")
        target_path = None

        # 决定使用哪个文件
        if os.path.exists(specific_path):
            target_path = specific_path
        elif os.path.exists(common_path):
            target_path = common_path
            flag_need_write = True
        else:
            # 两个都不存在：用默认配置写入专属文件
            target_path = specific_path
            flag_need_read = False
            flag_need_write = True

        # 读取文件
        if flag_need_read:
            with self._config_lock:
                with open(target_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)

        # 补全缺失的默认字段
        for key, value in self.default_config.items():
            if key not in config:
                config[key] = value

        if flag_need_write:
            target_path = specific_path
            with self._config_lock:
                with open(target_path, 'w', encoding='utf-8') as f:
                    json.dump(config, f, ensure_ascii=False, indent=4)
        return config

    def _load_bot_memory(self, bot_hash: str) -> dict:
        """
        加载指定 bot 的记忆，逻辑与配置类似：
        memory_{botHash}.json -> memory.json -> 默认空字典并写入
        """
        flag_need_read = True
        flag_need_write = False

        os.makedirs(self.memory_dir, exist_ok=True)

        memory = {}
        specific_path = os.path.join(self.memory_dir, f"memory_{bot_hash}.json")
        common_path = os.path.join(self.memory_dir, "memory.json")
        target_path = None

        if os.path.exists(specific_path):
            target_path = specific_path
        elif os.path.exists(common_path):
            target_path = common_path
            flag_need_write = True
        else:
            target_path = specific_path
            # 默认记忆为空字典，也可自定义默认记忆内容
            flag_need_read = False
            flag_need_write = True

        if flag_need_read:
            with self._memory_lock:
                with open(target_path, 'r', encoding='utf-8') as f:
                    memory = json.load(f)

        if flag_need_write:
            target_path = specific_path
            with self._memory_lock:
                with open(target_path, 'w', encoding='utf-8') as f:
                    json.dump(memory, f, ensure_ascii=False, indent=4)
        return memory

    def load_bot(self, bot_hash: str):
        """
        加载指定 bot 的配置和记忆，存入 self.config 和 self.memory
        """
        try:
            self.config[bot_hash] = self._load_bot_config(bot_hash)
            self.memory[bot_hash] = self._load_bot_memory(bot_hash)
        except Exception as e:
            # 日志可使用你已有的 logger，这里仅示意
            OlivOSAIChatAssassin.logger.warn(f"加载 bot {bot_hash} 失败: {e}")
            raise

    # ==================== 对外接口 ====================

    def reload(self, bot_hash: Optional[str] = None):
        """
        重新加载配置/记忆。
        - 若指定 bot_hash，则只重载该 bot；
        - 若为 None，则重载当前已加载的所有 bot（遍历 self.config.keys()）
        """
        if bot_hash is not None:
            self.load_bot(bot_hash)
        else:
            for bh in list(self.config.keys()):
                self.load_bot(bh)

    def get(self, data_type: DataManagerType, bot_hash: str) -> dict:
        """
        获取指定 bot 的配置或记忆字典。
        若 bot 尚未加载，会自动调用 load_bot 加载。
        """
        container = self._data_mapping.get(data_type)
        if container is None:
            raise ValueError(f"Unknown data type: {data_type}")

        # 自动加载未加载的 bot
        if bot_hash not in container:
            self.load_bot(bot_hash)

        return container[bot_hash]

    def getConfig(self, bot_hash: str) -> dict:
        return self.get(DataManagerType.CONFIG, bot_hash)

    def getMemory(self, bot_hash: str) -> dict:
        return self.get(DataManagerType.MEMORY, bot_hash)

    # ==================== 写入 / 持久化 ====================

    def save_bot_config(self, bot_hash: str):
        """将当前内存中的配置写回文件（覆盖专属文件）"""
        if bot_hash not in self.config:
            raise KeyError(f"Bot {bot_hash} 配置未加载")
        path = os.path.join(self.config_dir, f"config_{bot_hash}.json")
        with self._config_lock:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.config[bot_hash], f, ensure_ascii=False, indent=4)

    def save_bot_memory(self, bot_hash: str):
        """将当前记忆写回文件"""
        if bot_hash not in self.memory:
            raise KeyError(f"Bot {bot_hash} 记忆未加载")
        path = os.path.join(self.memory_dir, f"memory_{bot_hash}.json")
        with self._memory_lock:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.memory[bot_hash], f, ensure_ascii=False, indent=4)
