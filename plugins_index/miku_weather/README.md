# Miku 天气查询插件（miku_weather）

**只使用和风天气 API**（qweather）。返回一张带底图的初音蓝主题图片卡片。

> 旧代码里的 mock / OpenWeatherMap 已移除，仅保留和风天气。

## 指令

| 指令 | 说明 |
|---|---|
| `天气` | 查询默认城市天气（默认城市可在 `config/bot.yaml` 修改，默认值「北京」） |
| `天气 <城市名>` | 查询指定城市，例如 `天气 上海` / `天气 Tokyo` |

## 底图

把天气卡片的背景图放到以下任一位置（任选其一）：

```
utils/templates/weather_bg.png   ← 推荐
utils/templates/weather_bg.jpg
utils/templates/weather_bg.jpeg
```

- 推荐尺寸：**竖版**，约宽 720 × 高 900~1100 像素
- 找不到底图时，模板会自动回退到**初音蓝 CSS 渐变背景**，不会报错

## 申请和风天气 API Key

1. 去 <https://dev.qweather.com/> 注册账号
2. 创建「免费项目」得到 `API_KEY`
3. 在你的控制台里找到专属 `API_HOST`（免费项目一般是 `devapi.qweather.com`；如果是专属域名，例如 `xxx.re.qweatherapi.com`，请以你看到的为准）
4. 把这两项填到 `config/bot.yaml` 的 `miku_weather` 区

## 配置区

首次加载插件会在 `config/bot.yaml` 自动追加：

```yaml
miku_weather:
  # 和风天气 API Key（必填），申请地址：https://dev.qweather.com/
  API_KEY: ''
  # 用户未指定城市时的默认查询城市
  DEFAULT_CITY: 北京
  # 和风天气专属 API Host（必填），每个人的都不一样
  # 例如：devapi.qweather.com / api.qweather.com / xxx.re.qweatherapi.com
  API_HOST: devapi.qweather.com
  # 天气描述语言：zh（中文）/ en（英文）
  lang: zh
  # 响应风格：card（图片卡片）/ text（纯文本）
  response_style: card
```

> 字段名用大写 `API_KEY` / `API_HOST`，避免和旧配置中用的 `qweather_api_key` 混淆（那个字段已移除）。

## 工作原理

1. 用户发送 `天气 北京` → 解析城市名
2. 调用和风天气「城市检索」API，拿到第一个匹配城市的 `id`
3. 再调用「实时天气」API（使用你账户里的专属 `API_HOST`），得到温度、湿度、风速、天气图标代码等
4. 统一整理成卡片需要的字段，并处理 API 失败时的友好消息
5. 尝试加载 `weather_bg.png` 作为底图（base64 嵌入 HTML）→ 渲染模板 → `screenshot_html()` 截图
6. 以图片消息返回；如果浏览器不可用则回退到纯文本

## 卡片视觉

- **顶部**：MI KU 天气大标题（白色描边）
- **卡片**：毛玻璃白色卡片 + 初音蓝边框
- **城市 + icon**：例如「北京」+ emoji
- **温度大字**：52px 大字温度（例 26°C）
- **四栏细节**：最高温 / 最低温 / 湿度 / 风力
- **提示**：API Host / 更新时间

## 关键文件

| 文件 | 作用 |
|---|---|
| `plugins/miku_weather/__init__.py` | 指令入口、API 调用、模板渲染、截图发送；**自身持有 YAML 模板字符串 `_WEATHER_TEMPLATE`** |
| `utils/html_render.py` | HTML 模板变量替换 |
| `utils/screenshot.py` | Playwright 截图引擎 |
| `utils/templates/weather.html` | 天气卡片样式模板 |
| `utils/config_manager.py` | 插件配置区注册 / 热读取 |
