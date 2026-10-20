# CETUS V8.2 干净模块

本包保留ENERGY、RAD、SITE、UHI、WEA模块、运行所需参数/日程数据及Linux/Windows程序。
不含案例输入、计算结果、图纸、验证报告、测试缓存或Git历史。

安装：`pip install -r requirements.txt`。
运行：`python examples/run_v8_2.py scene.shp weather.epw --output outputs/my_case`。
默认先建立收敛首日状态，再连续推进目标日前七天气象。
光学先验见ENERGY/optical_priors.json，可通过BuildingInstance属性或surface options覆盖。
窗短波透射率使用WindowTransmittance；长波透射和窗蓄热为0。

源码版本：14702dc081ef1ed93bdc6727b96a2cb1e062f5be。模型实现与上一交付包一致，本次仅拆包。
