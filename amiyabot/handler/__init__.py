import os
import zipimport
import importlib

from typing import Type
from amiyabot import log
from amiyabot.util import append_sys_path

from .messageHandlerDefine import *


class BotHandlerFactory:
    def __init__(self):
        self.prefix_keywords = list()
        self.group_config = GroupConfigManager()

        self.event_handlers: Dict[str, List[EVENT_CORO]] = dict()
        self.message_handlers: List[MessageHandlerItem] = list()
        self.exception_handlers: Dict[Type[Exception], List[EXCEPTION_CORO]] = dict()

        self.after_reply_handlers: List[AFTER_CORO] = list()
        self.before_reply_handlers: List[BEFORE_CORO] = list()
        self.message_handler_middleware: List[MIDDLE_WARE] = list()

    def on_message(self,
                   group_id: Union[GroupConfig, str] = None,
                   keywords: KEYWORDS = None,
                   verify: VERIFY_CORO = None,
                   check_prefix: PREFIX = None,
                   allow_direct: Optional[bool] = None,
                   direct_only: bool = False,
                   level: int = 0):
        """
        注册消息处理器

        :param group_id:      组别 ID
        :param keywords:      触发关键字
        :param verify:        自定义校验方法
        :param check_prefix:  是否校验前缀或指定需要校验的前缀
        :param allow_direct:  是否支持用于私信
        :param direct_only:   是否仅支持私信
        :param level:         关键字校验成功后函数的候选默认等级
        :return:              注册函数的装饰器
        """

        def register(func: FUNC_CORO):
            _handler = MessageHandlerItem(func,
                                          self.group_config,
                                          level=level,
                                          group_id=str(group_id),
                                          direct_only=direct_only,
                                          allow_direct=allow_direct,
                                          check_prefix=check_prefix,
                                          prefix_keywords=self.prefix_keywords)
            if verify:
                _handler.custom_verify = verify
            else:
                _handler.keywords = keywords

            self.message_handlers.append(_handler)

        return register

    def on_event(self, events: Union[str, List[str]]):
        """
        事件响应注册器

        :param events: 事件名或事件名列表
        :return:
        """

        def register(func: EVENT_CORO):
            nonlocal events
            if type(events) is not list:
                events = [events]

            for item in events:
                if item not in self.event_handlers:
                    self.event_handlers[item] = []

                self.event_handlers[item].append(func)

        return register

    def on_exception(self, exceptions: Union[Type[Exception], List[Type[Exception]]] = Exception):
        """
        注册异常处理器，参数为异常类型或异常类型列表，在执行通过本实例注册的所有方法产生异常时会被调用

        :param exceptions: 异常类型或异常类型列表
        :return:           注册函数的装饰器
        """

        def handler(func: EXCEPTION_CORO):
            nonlocal exceptions
            if type(exceptions) is not list:
                exceptions = [exceptions]

            for item in exceptions:
                if item not in self.exception_handlers:
                    self.exception_handlers[item] = []

                self.exception_handlers[item].append(func)

        return handler

    def before_bot_reply(self, handler: BEFORE_CORO):
        """
        Bot 回复前处理，用于定义当 Bot 即将回复消息时的操作，该操作会在处理消息前执行

        :param handler: 处理函数
        :return:
        """
        self.before_reply_handlers.append(handler)

    def after_bot_reply(self, handler: AFTER_CORO):
        """
        Bot 回复后处理，用于定义当 Bot 回复消息后的操作，该操作会在发送消息后执行

        :param handler: 处理函数
        :return:
        """
        self.after_reply_handlers.append(handler)

    def handler_middleware(self, handler: MIDDLE_WARE):
        """
        Message 对象与消息处理器的中间件，用于对 Message 作进一步的客制化处理，允许存在多个，但会根据加载顺序叠加使用

        :param handler: 处理函数
        :return:
        """
        self.message_handler_middleware.append(handler)

    def set_group_config(self, config: GroupConfig):
        self.group_config.config[config.group_id] = config


class PluginInstance(BotHandlerFactory):
    def __init__(self, plugin_id: str, name: str, description: str = None):
        super().__init__()

        self.plugin_id = plugin_id

        self.name = name
        self.description = description


class BotInstance(BotHandlerFactory):
    def __init__(self,
                 appid: str = None,
                 token: str = None,
                 adapter: Type[BotAdapterProtocol] = None):
        super().__init__()

        self.instance: Optional[BotAdapterProtocol] = None
        if adapter:
            self.instance = adapter(appid, token)

        self.appid = appid
        self.plugins: Dict[str, PluginInstance] = {}

    def load_plugin(self, path: str):
        with log.sync_catch('plugin load error:'):
            if os.path.isdir(path):
                path_split = path.replace('\\', '/').split('/')
                append_sys_path(os.path.abspath('/'.join(path_split[:-1])))
                module = importlib.import_module(path_split[-1])
            elif path.endswith('.py'):
                append_sys_path(os.path.abspath(os.path.dirname(path)))
                module = importlib.import_module(os.path.basename(path).strip('.py'))
            elif path.endswith('.zip'):
                append_sys_path(os.path.abspath(path))
                module = zipimport.zipimporter(path).load_module('__init__')
            else:
                return None

            plugin: PluginInstance = getattr(module, 'plugin')

            self.combine_factory(self, plugin)
            self.plugins[plugin.plugin_id] = plugin

            return plugin

    @staticmethod
    def combine_factory(target: BotHandlerFactory, parent: BotHandlerFactory):
        target.prefix_keywords += parent.prefix_keywords
        target.message_handlers += parent.message_handlers
        target.after_reply_handlers += parent.after_reply_handlers
        target.before_reply_handlers += parent.before_reply_handlers
        target.message_handler_middleware += parent.message_handler_middleware

        target.group_config.config.update(parent.group_config.config)

        dict_handlers = [
            'event_handlers',
            'exception_handlers'
        ]
        for keyname in dict_handlers:
            for e in getattr(parent, keyname):
                if e not in getattr(target, keyname):
                    getattr(target, keyname)[e] = getattr(parent, keyname)[e]
                else:
                    getattr(target, keyname)[e] += getattr(parent, keyname)[e]
