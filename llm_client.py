"""llm_client.py — LLM 단발 호출 (data_manager/llm_client.py 이식본).

원본: data_manager/llm_client.py (그 원본은 tbm_system_v6/modules/llm_client.py).
변경점 하나뿐 — 시크릿을 `settings.secrets` 대신 **`app_config` 상수**에서 직접 읽는다.
process_designer 에는 settings.py 가 없고, app_config 는 전 repo 동일본이라 이쪽이 맞다.

폴백 순서는 **SOLA → Upstage → OpenAI** (공통규칙 5). 단일 클라이언트로 축약하지 말 것 —
provider 별 실패 사유를 누적해 조용한 실패를 표면화하는 게 이 설계의 핵심이다.
키가 없는 provider 는 자연히 건너뛰므로, 사내 SOLA 만 설정된 환경에서도 그대로 동작한다.

SOLA 금기 (전부 실제로 겪은 것 — 지우지 말 것):
  · `stream` 파라미터 추가 금지
  · 토큰 상한 **미전송** — `max_tokens` 복수형은 400 으로 거부되고, HTTP 200 +
    "please check parameter" 문자열이 LLM 답인 척 흘러간다(조용한 실패)
  · `proxies={"http": None, "https": None}` 로 프록시 우회 직접 접속.
    `proxies=None` 은 안 된다 — requests 가 시스템·레지스트리 프록시를 그대로 쓴다
  · 사내 자가서명 인증서 → `verify=False`
  · Qwen3 계열 응답의 `<think>...</think>` 는 정규식으로 제거 후 파싱
"""
from __future__ import annotations

import json
import os
import re

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ★ load_dotenv 를 직접 부르지 않는다 — .env 의 사내 프록시가 사외망에 주입돼
#   전 호출이 실패한다(공통규칙 5). app_config 가 망을 보고 정리한 값만 쓴다.
import app_config as cfg


def _req_proxies():
    """사내망 프록시 — OS 환경변수 → app_config 순으로 읽고, 호스트 있는 유효값만 사용.

    OpenAI SDK(httpx)는 사내 MITM 프록시에서 행(hang)이 발생 → requests 를 쓴다.
    스킴만 있는 불완전 값('http://')·미설정은 건너뛰고, 없으면 None(직접 접속, 사외망).
    """
    for c in (os.environ.get("HTTPS_PROXY"), os.environ.get("HTTP_PROXY"),
              os.environ.get("https_proxy"), os.environ.get("http_proxy"),
              cfg.HTTPS_PROXY, cfg.HTTP_PROXY):
        c = (c or "").strip()
        if "://" in c and c.split("://", 1)[1]:   # 호스트가 있는 유효 프록시
            return {"http": c, "https": c}
    return None


_PROXY_DEFAULT = object()   # _req_proxies() 사용 표시 (None = 명시적 직접접속과 구분)


def _openai_compat_chat(base_url: str, api_key: str, model: str, system: str,
                        user_prompt: str, max_tokens: int | None, temperature: float,
                        proxies=_PROXY_DEFAULT, response_format=None,
                        verify=None, timeout: int = 60) -> str:
    """OpenAI 호환 /chat/completions 를 requests 로 호출 (httpx 프록시 행 회피).

    model/max_tokens 가 비면 payload 에서 생략(SOLA 게이트웨이는 max_tokens 거부 → None 전달).
    proxies 미지정 시 _req_proxies(); SOLA 는 {"http":None,"https":None}(프록시 우회) 전달.
    verify 미지정 시 자동: 프록시 미경유(사외망 직접 접속)=True, 사내 프록시 경유=False
    (사내 SSL 인스펙션 대응). SOLA(사내 자가서명)는 호출부가 False 를 명시.
    """
    messages = [*([{"role": "system", "content": system}] if system else []),
                {"role": "user", "content": user_prompt}]
    body: dict = {"messages": messages, "temperature": temperature}
    if model:
        body["model"] = model
    if max_tokens:
        body["max_tokens"] = max_tokens
    if response_format:
        body["response_format"] = response_format
    px = _req_proxies() if proxies is _PROXY_DEFAULT else proxies
    if verify is None:
        verify = not (px and (px.get("http") or px.get("https")))
    resp = requests.post(
        base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body, proxies=px, timeout=timeout, verify=verify,
    )
    resp.raise_for_status()
    return (resp.json()["choices"][0]["message"]["content"] or "").strip()


def _call_sola(system: str, user_prompt: str, max_tokens: int | None, temperature: float,
               response_format=None, timeout: int = 60) -> str:
    """사내망 SOLA — OpenAI 호환 /chat/completions (Bearer, 프록시 우회, 자가서명 허용)."""
    base_url = cfg.LLM_SOLAR_API_URL
    api_key = cfg.LLM_SOLAR_API_KEY
    if not base_url or not api_key:
        raise RuntimeError(".env LLM_SOLAR_API_URL/LLM_SOLAR_API_KEY 미설정")
    return _openai_compat_chat(
        base_url, api_key, cfg.LLM_SOLAR_MODEL,   # model=LLM_SOLAR_MODEL (비면 생략)
        system, user_prompt,
        max_tokens=None,                          # ★ SOLA 는 토큰 상한을 거부한다 — 미전송
        temperature=temperature,
        proxies={"http": None, "https": None},    # ★ 사내망 SOLA 는 프록시 우회(직접 접속)
        response_format=response_format,
        verify=False,                             # 사내 자가서명 인증서 — SOLA 만 검증 생략
        timeout=timeout,
    )


def _call_upstage(system: str, user_prompt: str, max_tokens: int | None, temperature: float,
                  response_format=None, timeout: int = 60) -> str:
    """Upstage Solar (OpenAI 호환) — requests 로 호출 (사내망 프록시 대응)."""
    if not cfg.LLM_UPSTAGE_API_KEY:
        raise RuntimeError(".env LLM_UPSTAGE_API_KEY 미설정")
    return _openai_compat_chat(cfg.LLM_UPSTAGE_BASE_URL, cfg.LLM_UPSTAGE_API_KEY,
                               cfg.LLM_UPSTAGE_MODEL, system, user_prompt,
                               max_tokens, temperature, response_format=response_format,
                               timeout=timeout)


def _call_openai(system: str, user_prompt: str, max_tokens: int | None, temperature: float,
                 response_format=None, timeout: int = 60) -> str:
    if not cfg.LLM_OPENAI_API_KEY:
        raise RuntimeError(".env LLM_OPENAI_API_KEY 미설정")
    return _openai_compat_chat("https://api.openai.com/v1", cfg.LLM_OPENAI_API_KEY,
                               cfg.LLM_OPENAI_MODEL, system, user_prompt,
                               max_tokens, temperature, response_format=response_format,
                               timeout=timeout)


_PROVIDERS = (("sola", _call_sola), ("upstage", _call_upstage), ("openai", _call_openai))

# 마지막 호출의 provider별 실패 사유 — 진단/표면화용 (성공 시 빈 리스트)
_LLM_LAST_ERRORS: list[str] = []


def is_configured() -> bool:
    """LLM 을 쓸 수 있는가 (키가 하나라도 설정됐는가). 챗봇 탭 노출 여부 판단용.

    값 자체는 절대 돌려주지 않는다 — 설정 여부만 본다(시크릿 노출 금지).
    """
    return bool((cfg.LLM_SOLAR_API_KEY and cfg.LLM_SOLAR_API_URL)
                or cfg.LLM_UPSTAGE_API_KEY or cfg.LLM_OPENAI_API_KEY)


def provider_label() -> str:
    """설정된 provider 표시 문자열 (키 값은 노출하지 않는다)."""
    on = []
    if cfg.LLM_SOLAR_API_KEY and cfg.LLM_SOLAR_API_URL:
        on.append("사내 SOLA")
    if cfg.LLM_UPSTAGE_API_KEY:
        on.append("Upstage")
    if cfg.LLM_OPENAI_API_KEY:
        on.append("OpenAI")
    return " → ".join(on) if on else "미설정"


def last_errors() -> list[str]:
    """직전 호출의 provider별 실패 사유 (조용한 실패 표면화용)."""
    return list(_LLM_LAST_ERRORS)


def _chat(system: str, prompt: str, max_tokens: int | None, temperature: float,
          response_format=None, timeout: int = 60) -> str | None:
    """SOLA → Upstage → OpenAI 순으로 시도, 첫 성공의 raw 문자열. 전부 실패면 None."""
    global _LLM_LAST_ERRORS
    _LLM_LAST_ERRORS = []
    for name, fn in _PROVIDERS:
        try:
            content = fn(system, prompt, max_tokens, temperature, response_format, timeout)
            if content:
                return content
            _LLM_LAST_ERRORS.append(f"{name}: 빈 응답")
        except Exception as e:   # noqa: BLE001 — provider 실패는 다음 provider 로 넘긴다
            _LLM_LAST_ERRORS.append(f"{name}: {type(e).__name__}: {e}")
    return None


def _strip_think(content: str) -> str:
    """Qwen3 계열의 <think>…</think> 사고블록 제거 + 코드펜스 정리."""
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    content = re.sub(r"^```(?:json)?\s*", "", content)
    return re.sub(r"\s*```$", "", content).strip()


def call_llm(prompt: str,
             system: str = "당신은 조선소 시운전 업무 분석 전문가입니다. JSON 형식으로만 응답하세요.",
             max_tokens: int = 512,
             temperature: float = 0.3) -> dict | None:
    """구조화 응답용 — dict 를 돌려준다. 완전 실패 시 None (사유는 last_errors())."""
    content = _chat(system, prompt, max_tokens, temperature,
                    response_format={"type": "json_object"})
    if not content:
        return None
    content = _strip_think(content)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        _LLM_LAST_ERRORS.append(f"json 파싱 실패: {content[:120]}")
        return None


def call_llm_text(prompt: str, system: str = "", max_tokens: int = 1200,
                  temperature: float = 0.2, timeout: int = 90) -> str | None:
    """자연어 응답용 — 문자열을 그대로 돌려준다 (챗봇).

    call_llm 과 달리 `response_format=json_object` 를 걸지 않는다. JSON 을 강제하면
    사람이 읽을 답이 아니라 `{"answer": …}` 껍데기가 오고, 파싱 실패 시 답이 통째로 사라진다.
    스트리밍은 쓰지 않는다 — SOLA 금기이기도 하고, 이 앱은 이벤트 1건 = 왕복 1회 구조다.
    """
    content = _chat(system, prompt, max_tokens, temperature, timeout=timeout)
    return _strip_think(content) if content else None
