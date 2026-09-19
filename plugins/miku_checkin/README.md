# Miku 每日签到插件（miku_checkin）

每日签到系统：随机金币（1-20） + 随机好感度（0.10-1.00） + 3% 概率触发双倍好感度，同时支持「双倍好感度卡」道具（为以后的商店插件预留接口）。以精美的初音蓝主题图片卡片返回。

## 指令

| 指令 | 别名 | 说明 |
|---|---|---|
| `签到` | `checkin / 每日签到 / 打卡 / 每日打卡` | 执行每日签到（首次才发奖励） |

## 签到奖励

| 项目 | 当日首次签到 | 当日重复签到 |
|---|---|---|
| 💰 金币 | **1 ~ 20 随机**（整数） | 0 |
| 💖 好感度 | **0.10 ~ 1.00 随机**（2 位小数，累计上限 100.00） | 0 |
| 📅 累计签到天数 | **+1** | 不变 |
| ✅ 双倍好感度 | **3% 概率** 触发；或用「双倍好感度卡」100% 触发 | — |

> 📌 当日重复签到时，卡片上显示的「签到时间」将保持当天第一次签到的时间。

## 输出示例（图片卡片）

发送 `签到`，返回一张初音蓝主题卡片，包含：
- 📆 当前日期
- 🎵 昵称 + QQ号
- 📖 三个大字：累计签到天数 / 金币余额 / 好感度
- 💰 奖励行：`金币 +14  好感度 +0.63`
- ✅ 双倍好感度行（触发时显示）：
  - 用卡 → `✅ 双倍好感度卡已消耗：0.63 × 2 = 1.26`
  - 运气 → `✅ 幸运触发！好感度 0.63 → 1.26`
- 💝 道具行（有道具/buff 时显示）：`💝 双倍好感度卡 ×2  🎯 下次签到好感必双倍`
- ⏱ 当日**首次**签到时间（全天不变）
- 💬 随机「动漫语录」+ 出处

## 底图（必须放一张）

把 `.png / .jpg / .jpeg` 图片放到以下任一位置：

```
utils/templates/checkin_bg.png   ← 推荐
utils/templates/checkin_bg.jpg
utils/templates/checkin_bg.jpeg
```

- 推荐尺寸：**竖版**（宽 720 × 高 1150 像素左右）
- 找不到底图时，模板自动回退到纯 CSS 渐变背景，不会报错

## 配置区（`config/bot.yaml`）

首次加载插件会自动追加下面这段到 `config/bot.yaml`：

```yaml
miku_checkin:
  # 响应风格：card（图片卡片）/ text（纯文本）
  response_style: card
  # 每次签到获得的金币范围（最小-最大，整数）
  coins_min: 1
  coins_max: 20
  # 每次签到获得的好感度范围（最小-最大，保留 2 位小数）
  favor_min: 0.1
  favor_max: 1.0
  # 「双倍好感度」幸运触发概率（0-1，例如 0.03 = 3%）
  double_favor_prob: 0.03
  # 图片卡片宽度（像素）—— 仅 response_style=card 时生效
  card_width: 720
  # 图片卡片高度（像素）—— 仅 response_style=card 时生效
  card_height: 1150
```

## 数据存储

每个用户一个独立 JSON 文件，位置：

```
data/users/{QQ号}.json
```

结构示例：

```json
{
  "user_id": "10001",
  "total_checkin_days": 12,
  "coins": 158,
  "favor": 6.43,
  "last_first_time": "08:32:15",
  "last_checkin_date": "2025-06-19",
  "history": ["2025-06-01", "2025-06-02", "..." ],
  "items": {
    "double_favor_card": 2
  },
  "active_buffs": {
    "next_double_favor": true
  },
  "stats": {
    "total_coins_earned": 158,
    "total_favor_earned": 6.43,
    "double_favor_triggered": 1
  }
}
```

## 「双倍好感度卡」— 与未来的商店插件对接

- 道具名：`double_favor_card`（在 `items` 里计数）
- 激活后 buff：`next_double_favor`（在 `active_buffs` 里）
- **购买后使用**：下次签到时，好感度 **100% 双倍**，buff 自动消耗
- 商店插件只要调用以下三个函数即可完成买卖：

```python
from utils.user_store import add_coins, add_item, use_double_favor_card

# 1) 用户花 30 金币买一张卡
add_coins(user_id, -30)
add_item(user_id, "double_favor_card", 1)

# 2) 用户主动用一张卡
use_double_favor_card(user_id)  # 返回 True/False
```

公开接口完整列表（都在 `utils/user_store.py`）：

```python
from utils.user_store import (
    do_checkin,              # 完成一次签到，返回详细 dict
    get_user, get_balance, get_favor, get_items,
    add_coins, add_favor,
    add_item, use_item,
    set_buff, consume_buff, has_buff,
    use_double_favor_card,
)
```

## 自定义动漫语录

编辑 `utils/anime_quotes.py` 里的 `ANIME_QUOTES` 列表，每条格式：

```python
("只要有想见的人，就不再是孤身一人了。", "《夏目友人帐》"),
```

可以随意增删，签到时会从中随机取一条。

## 工作原理

1. 用户发送 `签到`
2. 调用 `utils.user_store.do_checkin(user_id)`：
   - 判断今天是否签过 → 签过直接返回旧时间，不发奖励
   - 没签 → 随机金币（1-20）、随机好感度（0.10-1.00）
   - 检查 `next_double_favor_buff` 是否激活 → 激活则 100% 双倍并消耗 buff
   - 否则按 `double_favor_prob` 概率随机双倍
   - 保存/更新 JSON 数据文件
3. `utils/anime_quotes.random_quote()` 随机语录
4. 根据结果组合奖励提示行（普通 / 用卡双倍 / 幸运双倍 / 重复签到）
5. 加载底图（base64 嵌入 HTML）→ 渲染模板 → `screenshot_html()` 截图
6. 以图片消息形式返回给用户

## 关键文件

| 文件 | 作用 |
|---|---|
| `plugins/miku_checkin/__init__.py` | 指令入口、奖励行组装、图片渲染与发送；**自身持有 YAML 模板字符串 `_CHECKIN_TEMPLATE`** |
| `utils/user_store.py` | **核心**：数据读写、随机奖励、双倍好感度、道具与金币接口 |
| `utils/anime_quotes.py` | 动漫语录数据（30+ 条预设） |
| `utils/html_render.py` | HTML 模板变量替换 |
| `utils/screenshot.py` | Playwright 截图引擎 |
| `utils/templates/checkin.html` | 签到卡片模板（含双倍好感度行样式） |
