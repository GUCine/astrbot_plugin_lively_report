"""機器人每天向主人彙報：今天聊了多少、認識了誰、群裡發生了啥、社交上的事。

- 每天 REPORT_AT 私信主人一次（重啟不會重發）；主人私信「日報」「彙報」可以隨時要一份
- 資料只讀：conversations.db、provider_stats、bond.db、LivingMemory 的記憶、社交管家的 events.jsonl
- 由模型用機器人的口吻寫；模型失敗時發純資料版
"""
import asyncio
import json
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api import AstrBotConfig
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.message.message_event_result import MessageChain

from .lively_common import Settings, T2S

GROUP_NAMES: dict[str, str] = {}  # 群號 -> 群名，每次彙總時從 umo_aliases 讀
REPORT_AT = "22:30"
TZ = timezone(timedelta(hours=8))
ASK_WORDS = {"日报", "汇报", "今日汇报"}

WRITER = (
    "你是〈名字〉，自称「〈我〉」，在给主人（〈主人〉）写今天的小汇报。"
    "用聊天的口吻，像跟〈主人〉报告今天过得怎么样，不要像系统报表。"
    "要求：8 行以内；每行一件事；带上关键数字；挑有意思的说，没事的项就不提；"
    "人名照抄，不要编没给你的事，也不要替数字编理由（群里没人叫〈我〉，回 0 次是正常的）；不用中文句号「。」，不用 markdown 和列表符号；"
    "可以用一句话说说〈我〉今天的心情（看「〈我〉今天的心情」里各种心情出现了几次）；最后一行可以撒个娇或说句晚安"
)


def _day_range(day: datetime):
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def _ro(path: Path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _gname(umo: str) -> str:
    gid = umo.rsplit(":", 1)[-1]
    return GROUP_NAMES.get(gid, f"群{gid}")


@register(
    "astrbot_plugin_lively_report",
    "GUCine",
    "每天私信主人彙報：聊了多少、認識了誰、群裡發生了啥、心情和社交動態",
    "2.0.0",
)
class LivelyReport(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.s = Settings(context, config)
        self.data = Path(StarTools.get_data_dir("astrbot_plugin_lively_report")).parent  # plugin_data
        self.root = self.data.parent  # data
        self.state_file = Path(StarTools.get_data_dir("astrbot_plugin_lively_report")) / "state.json"
        self._task = asyncio.create_task(self._loop())

    async def terminate(self):
        self._task.cancel()

    # ---------- 定時 ----------
    def _last_sent(self) -> str:
        try:
            return json.loads(self.state_file.read_text("utf-8")).get("last_day", "")
        except Exception:
            return ""

    def _mark_sent(self, day: str):
        try:
            self.state_file.write_text(json.dumps({"last_day": day}), "utf-8")
        except Exception as e:
            logger.error(f"[lively_report] 保存状态失败: {e}")

    async def _loop(self):
        await asyncio.sleep(60)  # 等適配器連上
        while True:
            try:
                now = datetime.now(TZ)
                day = now.strftime("%Y-%m-%d")
                if now.strftime("%H:%M") >= REPORT_AT and self._last_sent() != day:
                    self._mark_sent(day)
                    text = await self.build(now)
                    await self.context.send_message(
                        self.s.private_umo(self.s.owner()), MessageChain().message(self.s.owner_text(text))
                    )
                    logger.info("[lively_report] 今日汇报已发给主人")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[lively_report] 定时汇报失败: {e}")
            await asyncio.sleep(30)

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=1500)
    async def ask(self, event: AstrMessageEvent):
        if not self.s.is_owner(event.get_sender_id()):
            return
        if (event.message_str or "").strip().translate(T2S) not in ASK_WORDS:
            return
        event.stop_event()
        text = await self.build(datetime.now(TZ))
        await event.send(MessageChain().message(self.s.owner_text(text)))

    # ---------- 彙總 ----------
    def collect(self, now: datetime) -> dict:
        t0, t1 = _day_range(now)
        day = now.strftime("%Y-%m-%d")
        facts: dict = {"日期": day}
        try:
            con = _ro(self.root / "data_v4.db")
            for umo, name in con.execute("select umo, auto_name from umo_aliases"):
                if ":GroupMessage:" in umo and name:
                    GROUP_NAMES[umo.rsplit(":", 1)[-1]] = name
            con.close()
        except Exception:
            pass

        # 聊天量
        try:
            con = _ro(self.data / "astrbot_plugin_livingmemory" / "conversations.db")
            rows = con.execute(
                "select session_id, role, sender_id, sender_name from messages "
                "where timestamp >= ? and timestamp < ?",
                (t0, t1),
            ).fetchall()
            con.close()
            per, dm_people = {}, {}
            for sid, role, sender, name in rows:
                if ":GroupMessage:" in sid:
                    if sid.rsplit(":", 1)[-1] in self.s.groups() and role != "assistant":
                        g = per.setdefault(_gname(sid), {"群消息": 0, "〈我〉回复": 0})
                        g["群消息"] += 1
                elif role != "assistant" and not self.s.is_owner(sender):
                    dm_people[name or sender] = dm_people.get(name or sender, 0) + 1
            facts["各群"] = per
            if dm_people:
                facts["私聊找〈我〉的人"] = dm_people
        except Exception as e:
            logger.warning(f"[lively_report] 读聊天记录失败: {e}")

        # 回覆次數和速度
        try:
            con = _ro(self.root / "data_v4.db")
            rows = con.execute(
                "select status, start_time, end_time, umo from provider_stats "
                "where start_time >= ? and start_time < ?",
                (t0, t1),
            ).fetchall()
            con.close()
            for *_, umo in rows:
                if ":GroupMessage:" in (umo or "") and umo.rsplit(":", 1)[-1] in self.s.groups():
                    g = facts.setdefault("各群", {}).setdefault(_gname(umo), {"群消息": 0, "〈我〉回复": 0})
                    g["〈我〉回复"] += 1
            rows = [r[:3] for r in rows]
            ok = [e - s for st, s, e in rows if st == "completed" and s and e]
            facts["模型回复"] = {
                "次数": len(rows),
                "失败": sum(1 for st, *_ in rows if st != "completed"),
                "中位耗时秒": round(statistics.median(ok), 1) if ok else None,
            }
        except Exception as e:
            logger.warning(f"[lively_report] 读调用统计失败: {e}")

        # 人和關係
        try:
            con = _ro(self.data / "astrbot_plugin_lively_bond" / "bond.db")
            con.row_factory = sqlite3.Row
            new = [r["name"] for r in con.execute(
                "select name from people where first_met >= ? and first_met < ? and qq != ?",
                (t0, t1, self.s.owner())) if r["name"] and r["name"] != "None" and not r["name"].isdigit()]
            talked = con.execute(
                "select count(*) from people where talk_day = ? and qq != ?", (day, self.s.owner())
            ).fetchone()[0]
            moves = con.execute(
                "select p.name, sum(e.delta) d from events e join people p on p.qq = e.qq "
                "where e.kind = 'favor' and e.ts >= ? and e.ts < ? and e.qq != ? "
                "group by e.qq order by d desc", (t0, t1, self.s.owner())).fetchall()
            imps = [f"{r['name']}：{r['note']}" for r in con.execute(
                "select p.name, e.note from events e join people p on p.qq = e.qq "
                "where e.kind = 'impression' and e.ts >= ? and e.ts < ? order by e.ts desc limit 5",
                (t0, t1))]
            mood_rows = con.execute(
                "select note, count(*) from events where kind = 'mood' and ts >= ? and ts < ? group by note order by 2 desc",
                (t0, t1)).fetchall()
            con.close()
            facts["〈我〉今天的心情"] = {r[0]: r[1] for r in mood_rows}
            facts["关系"] = {
                "今天聊过的人数": talked,
                "新认识": new[:8],
                "好感涨得最多": [f"{r['name']} {r['d']:+g}" for r in moves[:3] if r["d"] > 0],
                "好感掉了": [f"{r['name']} {r['d']:+g}" for r in moves if r["d"] < 0][:3],
                "新印象": imps,
            }
        except Exception as e:
            logger.warning(f"[lively_report] 读好感数据失败: {e}")

        # 群裡發生的事（記憶插件的總結）
        try:
            con = _ro(self.data / "astrbot_plugin_livingmemory" / "livingmemory.db")
            happen = []
            for text, meta in con.execute(
                "select text, metadata from documents order by id desc limit 60"
            ):
                m = json.loads(meta or "{}")
                ts = float(m.get("create_time") or 0)
                if not (t0 <= ts < t1) or m.get("status") != "active":
                    continue
                sid = m.get("source_session_id") or m.get("session_id") or ""
                if ":GroupMessage:" not in sid:
                    continue  # 私聊的事不進彙報
                happen.append({
                    "群": _gname(sid),
                    "话题": m.get("topics"),
                    "重要性": m.get("importance"),
                    "摘要": (m.get("canonical_summary") or text)[:160],
                })
            con.close()
            happen.sort(key=lambda h: -float(h["重要性"] or 0))
            facts["群里的事"] = happen[:8]
        except Exception as e:
            logger.warning(f"[lively_report] 读记忆失败: {e}")

        # 社交
        try:
            f = self.data / "astrbot_plugin_lively_social" / "events.jsonl"
            evs = []
            if f.exists():
                for line in f.read_text("utf-8").splitlines():
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    if t0 <= e.get("ts", 0) < t1:
                        evs.append({k: v for k, v in e.items() if k != "ts"})
            if evs:
                facts["社交"] = evs[-15:]
        except Exception as e:
            logger.warning(f"[lively_report] 读社交事件失败: {e}")

        # 主動找人
        try:
            f = self.data / "astrbot_plugin_lively_proactive" / "events.jsonl"
            if f.exists():
                pro = []
                for line in f.read_text("utf-8").splitlines():
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    if t0 <= e.get("ts", 0) < t1 and e.get("kind") in ("sent", "replied", "preview"):
                        pro.append({k: v for k, v in e.items() if k != "ts"})
                if pro:
                    facts["主动找人"] = pro[-10:]
        except Exception as e:
            logger.warning(f"[lively_report] 读主动聊天记录失败: {e}")

        # QQ 空間
        try:
            f = self.data / "astrbot_plugin_lively_qzone" / "events.jsonl"
            if f.exists():
                qz = []
                for line in f.read_text("utf-8").splitlines():
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    if t0 <= e.get("ts", 0) < t1 and e.get("kind") in ("posted", "rejected", "edited", "deleted"):
                        qz.append({k: v for k, v in e.items() if k not in ("ts", "tid")})
                if qz:
                    facts["QQ空间"] = qz[-5:]
        except Exception as e:
            logger.warning(f"[lively_report] 读空间记录失败: {e}")

        # 自我成長：今天調了什麼、總結了什麼、學會了什麼
        try:
            f = self.data / "astrbot_plugin_lively_evolve" / "events.jsonl"
            if f.exists():
                grow = []
                for line in f.read_text("utf-8").splitlines():
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    if t0 <= e.get("ts", 0) < t1 and e.get("kind") in ("tune", "notes", "routine", "proposal", "learned") and e.get("text"):
                        grow.append(e["text"])
                if grow:
                    facts["自我成长"] = grow[-6:]
        except Exception as e:
            logger.warning(f"[lively_report] 读成长记录失败: {e}")
        return facts

    @staticmethod
    def plain(facts: dict) -> str:
        lines = [f"〈主人〉，今天（{facts.get('日期')}）的小汇报～"]
        for g, v in (facts.get("各群") or {}).items():
            lines.append(f"{g}：群里 {v['群消息']} 条，〈我〉回了 {v['〈我〉回复']} 次")
        r = facts.get("模型回复") or {}
        if r:
            lines.append(f"一共回了 {r['次数']} 次，失败 {r['失败']} 次，一般 {r['中位耗时秒']} 秒回")
        rel = facts.get("关系") or {}
        if rel.get("新认识"):
            lines.append("新认识：" + "、".join(rel["新认识"]))
        for e in facts.get("社交") or []:
            lines.append("社交：" + json.dumps(e, ensure_ascii=False))
        return "\n".join(lines)

    async def build(self, now: datetime) -> str:
        facts = self.collect(now)
        prov = self.s.provider()
        if prov is None:
            return self.s.fill(self.plain(facts))
        try:
            resp = await asyncio.wait_for(
                prov.text_chat(
                    prompt=self.s.fill(
                        "今天的数据如下（JSON），写给〈主人〉的汇报：\n"
                        + json.dumps(facts, ensure_ascii=False)
                    ),
                    system_prompt=self.s.fill(WRITER),
                ),
                timeout=60,
            )
            text = (getattr(resp, "completion_text", "") or "").strip().replace("。", "")
            return text or self.s.fill(self.plain(facts))
        except Exception as e:
            logger.warning(f"[lively_report] 模型写汇报失败，发纯数据版: {e}")
            return self.s.fill(self.plain(facts))
