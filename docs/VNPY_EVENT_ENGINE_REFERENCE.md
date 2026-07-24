# vn.py event engine design reference

DyadTetrade 的事件引擎与网关生命周期参考 vn.py/VeighNa 的公开架构，但没有复制 vn.py 源码，也没有把 vn.py 作为运行时依赖。

本轮核对的上游文件：

- [`vnpy/event/engine.py`](https://github.com/vnpy/vnpy/blob/master/vnpy/event/engine.py)：事件队列、按类型处理器、通用处理器与定时事件。
- [`vnpy/trader/engine.py`](https://github.com/vnpy/vnpy/blob/master/vnpy/trader/engine.py)：`MainEngine`、`BaseEngine`、`OmsEngine` 及活动委托投影。
- [`vnpy/trader/gateway.py`](https://github.com/vnpy/vnpy/blob/master/vnpy/trader/gateway.py)：非阻塞、线程安全的网关方法和回调边界，以及关闭时释放资源。

DyadTetrade 在既有 `EventBus` 上加入通用监听、有限历史、有限死信、显式队列背压、运行统计和统一关闭；QMT 回调仍只做转换与入队，订单必须继续经过发布门禁、风控、OMS 和网关隔离。
