import OlivOSAIChatAssassin


class Event:
    def init(plugin_event, Proc):
        # 初始化流程
        pass

    def init_after(plugin_event, Proc):
        # 初始化后处理流程
        OlivOSAIChatAssassin.data.gProc = Proc
        OlivOSAIChatAssassin.load.load_config()
        OlivOSAIChatAssassin.load.load_staticKnowledge()
        OlivOSAIChatAssassin.load.load_memory()
        # 初始化消息历史
        OlivOSAIChatAssassin.data.gMessageHistory = {}

    def private_message(plugin_event, Proc):
        # 私聊消息事件入口
        pass  # 本插件仅处理群聊

    def group_message(plugin_event, Proc):
        OlivOSAIChatAssassin.msg.unity_group_message(plugin_event, Proc)

    def poke(plugin_event, Proc):
        # 戳一戳事件入口
        pass

    def save(plugin_event, Proc):
        # 插件卸载时执行的保存流程
        pass

    def menu(plugin_event, Proc):
        # 插件菜单事件监听
        if plugin_event.data.namespace == 'OlivOSAIChatAssassin':
            if plugin_event.data.event == 'OlivOSAIChatAssassin_Menu':
                pass
            elif plugin_event.data.event == 'OlivOSAIChatAssassin_Status':
                status = OlivOSAIChatAssassin.msg.get_status()
                OlivOSAIChatAssassin.logger.log(status)
