# 每日彙報

> 每天私信主人彙報：聊了多少、認識了誰、群裡發生了啥、心情和社交動態

AstrBot 插件，屬於「擬真群友」（lively）插件套件。適用 AstrBot >=4.29.0 + NapCat（aiocqhttp）。

主人、群、機器人名字、人設用詞這些共用設定在 [astrbot_plugin_lively_gate](https://github.com/GUCine/astrbot_plugin_lively_gate) 裡填，建議一起裝。

## 功能

```text
機器人每天向主人彙報：今天聊了多少、認識了誰、群裡發生了啥、社交上的事。

- 每天 REPORT_AT 私信主人一次（重啟不會重發）；主人私信「日報」「彙報」可以隨時要一份
- 資料只讀：conversations.db、provider_stats、bond.db、LivingMemory 的記憶、社交管家的 events.jsonl
- 由模型用機器人的口吻寫；模型失敗時發純資料版
```

## 依賴

讀取 astrbot_plugin_lively_bond、astrbot_plugin_lively_social、astrbot_plugin_lively_evolve 和 LivingMemory 的資料，沒裝的部分不寫。

## 配置

在 AstrBot WebUI 的插件配置裡填，或者直接編輯 `data/config/astrbot_plugin_lively_report_config.json`。留空的公共項（主人、群、機器人名字、平台）會用 astrbot_plugin_lively_gate 裡填的。

| 配置項 | 說明 | 預設 |
| --- | --- | --- |
| `provider_id` | 寫日報用的模型：留空用 AstrBot 預設模型 | （空） |
| `groups` | 服務的群（留空用門禁插件裡填的） | （空） |
| `owner_qq` | 主人 QQ（留空用門禁插件裡填的） | （空） |

## 資料

執行資料在 `data/plugin_data/astrbot_plugin_lively_report/`。

## 許可

MIT，見 [LICENSE](LICENSE)。
