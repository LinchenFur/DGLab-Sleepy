"""DG-Lab integration for Sleepy Project 5.x.

This plugin ports the public "lightning" trigger from the legacy
DGLab-Sleepy fork to Sleepy's current plugin architecture.  It keeps the
legacy endpoints while adding namespaced v5 endpoints.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from html import escape
from logging import getLogger
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import flask
from flask_cors import cross_origin
from pydantic import BaseModel, Field, model_validator

import plugin as pl
import utils as u

l = getLogger(__name__)


class RangeConfig(BaseModel):
    min: int
    max: int

    @model_validator(mode="after")
    def validate_range(self):
        if self.min > self.max:
            raise ValueError("min cannot be greater than max")
        return self


class TurnstileConfig(BaseModel):
    enabled: bool = False
    site_key: str = ""
    secret_key: str = ""


class DGLabConfig(BaseModel):
    enabled: bool = True
    api_url: str = "http://127.0.0.1:8920/api/v2/game/all/action/fire"
    request_timeout: float = Field(default=10.0, gt=0, le=60)
    public_trigger: bool = True
    show_index_card: bool = True
    strength: RangeConfig = RangeConfig(min=1, max=30)
    duration: RangeConfig = RangeConfig(min=1, max=3)
    continuous_click: bool = True
    cooldown: RangeConfig = RangeConfig(min=3, max=8)
    rate_limit_count: int = Field(default=5, ge=1, le=100)
    rate_limit_window: int = Field(default=60, ge=1, le=3600)
    turnstile: TurnstileConfig = TurnstileConfig()

    @model_validator(mode="after")
    def validate_dglab_ranges(self):
        # DG-Lab Coyote Game Hub accepts strength 1-40 and duration <= 30 s.
        if self.strength.min < 1 or self.strength.max > 40:
            raise ValueError("strength must stay between 1 and 40")
        if self.duration.min < 1 or self.duration.max > 30:
            raise ValueError("duration must stay between 1 and 30 seconds")
        if self.turnstile.enabled and (not self.turnstile.site_key or not self.turnstile.secret_key):
            raise ValueError("turnstile site_key and secret_key are required when enabled")
        return self


def _load_legacy_config() -> dict[str, Any]:
    """Read the old DGLab.json format for zero-config upgrades."""
    path = Path(u.get_path("DGLab.json", create_dirs=False))
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as file:
            raw = json.load(file)
        return {
            "api_url": raw.get("api", {}).get("url"),
            "strength": raw.get("strength"),
            "duration": raw.get("time"),
            "continuous_click": raw.get("ui", {}).get("continuous_click"),
            "cooldown": raw.get("ui", {}).get("cooldown"),
        }
    except Exception:
        return {}


plugin = pl.Plugin(
    name="dglab",
    require_version_min=(5, 2, 0),
    require_version_max=(6, 0, 0),
    config=DGLabConfig,
    data={"total_triggers": 0, "successful_triggers": 0, "failed_triggers": 0, "last_triggered": None},
)

# A v5 config takes precedence.  If none exists, transparently migrate the
# repository-root DGLab.json used by the old fork.
if not pl.PluginInit.instance.c.plugin.get("dglab", {}):
    legacy = {key: value for key, value in _load_legacy_config().items() if value is not None}
    if legacy:
        plugin.config = DGLabConfig.model_validate(legacy)

config: DGLabConfig = plugin.config

# Cloud/container deployments can override the local DGLab.json endpoint with
# a secret environment variable without committing a private tunnel URL.
if os.getenv("DGLAB_API_URL"):
    config.api_url = os.environ["DGLAB_API_URL"].strip()

_rate_lock = threading.Lock()
_rate_buckets: defaultdict[str, deque[float]] = defaultdict(deque)

SUCCESS_MESSAGES = ("以雷霆，击碎黑暗！", "十万伏特！", "此刻，寂灭之时！")
FAILURE_MESSAGES = ("DG-Lab 服务没有响应。", "触发失败，请稍后重试。", "郊狼控制端当前不可用。")


def _client_ip() -> str:
    forwarded = flask.request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
    return forwarded or flask.request.headers.get("X-Real-IP", "").strip() or flask.request.remote_addr or "unknown"


def _check_rate_limit() -> int | None:
    now = time.monotonic()
    cutoff = now - config.rate_limit_window
    ip = _client_ip()
    with _rate_lock:
        bucket = _rate_buckets[ip]
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= config.rate_limit_count:
            return max(1, int(config.rate_limit_window - (now - bucket[0])))
        bucket.append(now)
    return None


def _has_secret() -> bool:
    expected = plugin.global_config.main.secret
    if not expected:
        return False
    body = flask.request.get_json(silent=True) or {}
    auth = flask.request.headers.get("Authorization", "")
    return any((
        body.get("secret") == expected,
        flask.request.args.get("secret") == expected,
        flask.request.headers.get("Sleepy-Secret") == expected,
        auth.startswith("Bearer ") and auth[7:] == expected,
        flask.request.cookies.get("sleepy-secret") == expected,
    ))


def _verify_turnstile(token: str) -> bool:
    if not config.turnstile.enabled:
        return True
    if not token:
        return False
    body = urlencode({
        "secret": config.turnstile.secret_key,
        "response": token,
        "remoteip": _client_ip(),
    }).encode("utf-8")
    request = Request(
        "https://challenges.cloudflare.com/turnstile/v0/siteverify",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=config.request_timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
        return result.get("success") is True
    except (HTTPError, URLError, TimeoutError, ValueError):
        return False


def _record_result(success: bool):
    with plugin.data_context() as data:
        data["total_triggers"] = int(data.get("total_triggers", 0)) + 1
        key = "successful_triggers" if success else "failed_triggers"
        data[key] = int(data.get(key, 0)) + 1
        data["last_triggered"] = datetime.now().astimezone().isoformat(timespec="seconds")


def _fire() -> tuple[dict[str, Any], int]:
    strength = random.randint(config.strength.min, config.strength.max)
    duration_seconds = random.randint(config.duration.min, config.duration.max)
    payload = {"strength": strength, "time": duration_seconds * 1000}
    request = Request(
        config.api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=config.request_timeout) as response:
            upstream_body = response.read().decode("utf-8", errors="replace")
            upstream_status = response.status
        if not 200 <= upstream_status < 300:
            raise RuntimeError(f"DG-Lab returned HTTP {upstream_status}")
        try:
            upstream: Any = json.loads(upstream_body) if upstream_body else None
        except ValueError:
            upstream = upstream_body[:500]
        cooldown = 0 if config.continuous_click else random.randint(config.cooldown.min, config.cooldown.max)
        result = {
            "success": True,
            "message": random.choice(SUCCESS_MESSAGES),
            "details": f"强度: {strength}，持续: {duration_seconds} 秒",
            "continuous_click": config.continuous_click,
            "cooldown_time": cooldown,
            "data_sent": payload,
            "upstream": upstream,
        }
        _record_result(True)
        return result, 200
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        message = f"DG-Lab 服务返回 HTTP {error.code}"
        if detail:
            message += f"：{detail}"
    except (URLError, TimeoutError) as error:
        message = f"无法连接 DG-Lab 服务：{getattr(error, 'reason', error)}"
    except Exception as error:
        message = f"DG-Lab 触发失败：{error}"

    _record_result(False)
    return {"success": False, "message": message or random.choice(FAILURE_MESSAGES)}, 502


def _trigger_response():
    if not config.enabled:
        return flask.jsonify(success=False, message="DG-Lab 插件未启用"), 503
    if not config.public_trigger and not _has_secret():
        return flask.jsonify(success=False, message="需要 Sleepy 密钥"), 401

    retry_after = _check_rate_limit()
    if retry_after is not None:
        response = flask.jsonify(success=False, message=f"请求过于频繁，请在 {retry_after} 秒后重试")
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        return response

    body = flask.request.get_json(silent=True) or {}
    token = body.get("cf-turnstile-response") or flask.request.form.get("cf-turnstile-response", "")
    if not _verify_turnstile(token):
        return flask.jsonify(success=False, message="人机验证失败，请重试"), 400

    result, status = _fire()
    return flask.jsonify(result), status


def _public_config():
    return {
        "success": True,
        "enabled": config.enabled,
        "public_trigger": config.public_trigger,
        "continuous_click": config.continuous_click,
        "cooldown": config.cooldown.model_dump(),
        "rate_limit": {"count": config.rate_limit_count, "window": config.rate_limit_window},
        "turnstile": {"enabled": config.turnstile.enabled, "site_key": config.turnstile.site_key},
    }


@plugin.global_route("/api/dglab/lightning", methods=["POST"], endpoint="api_dglab_lightning")
@cross_origin(plugin.global_config.main.cors_origins)
def api_dglab_lightning():
    return _trigger_response()


@plugin.global_route("/dglab/lightning", methods=["POST"], endpoint="legacy_dglab_lightning")
@cross_origin(plugin.global_config.main.cors_origins)
def legacy_dglab_lightning():
    return _trigger_response()


@plugin.global_route("/api/dglab/config", methods=["GET"], endpoint="api_dglab_config")
@cross_origin(plugin.global_config.main.cors_origins)
def api_dglab_config():
    return flask.jsonify(_public_config())


@plugin.global_route("/dglab/config", methods=["GET"], endpoint="legacy_dglab_config")
@cross_origin(plugin.global_config.main.cors_origins)
def legacy_dglab_config():
    return flask.jsonify(_public_config())


@plugin.global_route("/api/dglab/stats", methods=["GET"], endpoint="api_dglab_stats")
@cross_origin(plugin.global_config.main.cors_origins)
def api_dglab_stats():
    return flask.jsonify(success=True, **plugin.data)


@plugin.global_route("/dglab/stats", methods=["GET"], endpoint="legacy_dglab_stats")
@cross_origin(plugin.global_config.main.cors_origins)
def legacy_dglab_stats():
    return flask.jsonify(success=True, **plugin.data)



def _migrate_legacy_data():
    """Import the old root data.json once into the v5 SQLite database."""
    if plugin.get_data("legacy_data_migrated", False):
        return
    path = Path(u.get_path("data.json", create_dirs=False))
    if not path.is_file():
        return
    try:
        with path.open("r", encoding="utf-8") as file:
            legacy = json.load(file)

        data = plugin.global_data
        # Avoid overwriting an already-used v5 database.
        if data.device_list or data.status_id != 0:
            l.warning("[dglab] Found legacy data.json, but v5 already has status data; skipping automatic import")
            plugin.set_data("legacy_data_migrated", True)
            return

        status_id = legacy.get("status")
        if isinstance(status_id, int) and data.get_status(status_id)[0]:
            data.status_id = status_id
        if isinstance(legacy.get("private_mode"), bool):
            data.private_mode = legacy["private_mode"]

        imported = 0
        for device_id, device in (legacy.get("device_status") or {}).items():
            if not isinstance(device, dict):
                continue
            data.device_set(
                id=str(device_id),
                show_name=str(device.get("show_name") or device_id),
                using=device.get("using") if isinstance(device.get("using"), bool) else None,
                status=device.get("status") or device.get("app_name"),
                fields=device.get("fields") if isinstance(device.get("fields"), dict) else {},
            )
            imported += 1

        with plugin.data_context() as plugin_data:
            plugin_data["legacy_data_migrated"] = True
            plugin_data["legacy_devices_imported"] = imported
        l.info(f"[dglab] Imported legacy data.json ({imported} device(s))")
    except Exception as error:
        l.warning(f"[dglab] Could not import legacy data.json: {error}")

def _card_html() -> str:
    if not config.enabled or not config.show_index_card or not config.public_trigger:
        return ""
    turnstile = ""
    turnstile_script = ""
    if config.turnstile.enabled:
        turnstile = f'<div class="cf-turnstile" data-sitekey="{escape(config.turnstile.site_key)}"></div>'
        turnstile_script = '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>'
    return f"""
<style>
#dglab-trigger {{ padding: .7rem 1.2rem; border-radius: 999px; border: 1px solid currentColor; font-weight: 700; }}
#dglab-result {{ min-height: 1.5em; margin-top: .75rem; }}
#dglab-hint {{ opacity: .72; font-size: .85em; }}
</style>
<h2>⚡ DG-Lab</h2>
<p id="dglab-hint">每 {config.rate_limit_window} 秒最多触发 {config.rate_limit_count} 次，强度范围 {config.strength.min}–{config.strength.max}。</p>
{turnstile}
<button id="dglab-trigger" type="button">雷元素攻击</button>
<div id="dglab-result" role="status" aria-live="polite"></div>
{turnstile_script}
<script>
(() => {{
  const button = document.getElementById('dglab-trigger');
  const result = document.getElementById('dglab-result');
  if (!button || !result) return;
  button.addEventListener('click', async () => {{
    if (button.dataset.busy === 'true') return;
    button.dataset.busy = 'true';
    button.disabled = true;
    result.textContent = '正在触发…';
    const token = document.querySelector('[name="cf-turnstile-response"]')?.value || '';
    try {{
      const response = await fetch('/api/dglab/lightning', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{'cf-turnstile-response': token}})
      }});
      const data = await response.json();
      result.textContent = data.details ? `${{data.message}} ${{data.details}}` : data.message;
      const wait = data.success && !data.continuous_click ? Number(data.cooldown_time || 0) : 0;
      if (wait > 0) {{
        let left = wait;
        button.textContent = `冷却中 (${{left}}s)`;
        const timer = setInterval(() => {{
          left -= 1;
          button.textContent = left > 0 ? `冷却中 (${{left}}s)` : '雷元素攻击';
          if (left <= 0) {{ clearInterval(timer); button.disabled = false; button.dataset.busy = 'false'; }}
        }}, 1000);
      }} else {{
        button.disabled = false;
        button.dataset.busy = 'false';
      }}
      if (window.turnstile) window.turnstile.reset();
    }} catch (error) {{
      result.textContent = `请求失败：${{error}}`;
      button.disabled = false;
      button.dataset.busy = 'false';
    }}
  }});
}})();
</script>
"""


def _panel_html() -> str:
    data = plugin.data
    visibility = "公开触发" if config.public_trigger else "仅认证用户"
    return f"""
<p><b>状态：</b>{'已启用' if config.enabled else '已停用'} · {visibility}</p>
<p><b>接口：</b><code>/api/dglab/lightning</code>（兼容 <code>/dglab/lightning</code>）</p>
<p><b>上游：</b><code>{escape(config.api_url)}</code></p>
<p><b>触发统计：</b>{int(data.get('successful_triggers', 0))} 成功 / {int(data.get('failed_triggers', 0))} 失败 / {int(data.get('total_triggers', 0))} 总计</p>
<p><b>最后触发：</b>{escape(str(data.get('last_triggered') or '尚未触发'))}</p>
<p>配置位置：<code>data/config.toml</code> 的 <code>[plugin.dglab]</code>。</p>
"""


def init():
    _migrate_legacy_data()
    plugin.add_index_card("dglab", _card_html)
    plugin.add_panel_card("dglab-admin", "DG-Lab", _panel_html)


plugin.init = init
