"""LLM 韧性层（P1：熔断 + 重试 + 模型降级链，见 docs/LLM调用设计方案.md §5-§6）。

统一职责（业务层只声明 scenario，不感知重试/熔断细节）：
  1. CircuitBreaker  三态熔断器，按 (provider, model) 粒度，asyncio 安全；
  2. 模型链路由      scenario → [主模型, fallback...]（config.model_roles），
                     每级独立熔断，已熔断的模型快速跳过；
  3. resilient_invoke         非流式执行：同模型退避重试 → 逐级 fallback；
  4. resilient_invoke_stream  流式执行：仅首 chunk 前允许透明重试/换模型
                              （首 chunk 后失败直接抛，避免向用户重复吐字，修 P1-5）；
  5. 观测            scenario/model/outcome 指标 + 结构化调用日志。

错误处理契约（llm/errors.py 四分类）：
  RETRYABLE         同模型退避重试（尊重 Retry-After），耗尽后切 fallback；
  CONTEXT_OVERFLOW  不重试直接切 fallback（更长的模型可能救回来）；无 fallback 时抛给
                    调用方（agent 侧压缩历史后重试）；
  FATAL             不重试，切 fallback（换 provider 可能解决 401/404），不计熔断失败；
  LOGIC             本地 bug，立即抛出，绝不能被重试/降级掩盖。
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from shared import log

from codegenx.ai_service.llm.async_client import get_llm
from codegenx.ai_service.llm.errors import LLMErrorClass, classify_llm_error
from codegenx.ai_service.utils.config import config

# 场景常量（config.model_roles 的键）
SCENARIO_AGENT = "agent_main"
SCENARIO_COMPACT = "compact"
SCENARIO_SUMMARY = "summary"
SCENARIO_MEMORY = "memory"


class LLMNoModelAvailableError(RuntimeError):
    """链上所有模型均不可用（熔断打开或重试/降级耗尽）。"""


# ── 指标埋点（守卫式：埋点绝不阻断业务）────────────────────────────────────

def _metrics():
    from codegenx.ai_service.monitor import prometheus_metrics as m
    return m


def _inc(name: str, **labels) -> None:
    try:
        getattr(_metrics(), name).labels(**labels).inc()
    except Exception:  # noqa: BLE001
        pass


def _set_gauge(name: str, value: float, **labels) -> None:
    try:
        getattr(_metrics(), name).labels(**labels).set(value)
    except Exception:  # noqa: BLE001
        pass


# ── 三态熔断器 ──────────────────────────────────────────────────────────────

class CircuitBreaker:
    """closed →(连续失败≥阈值)→ open →(冷却期)→ half_open →(探测达标)→ closed。

    asyncio 单线程内状态迁移经过 asyncio.Lock 串行化，防止未来加入 await 点后
    出现 check-then-act 竞态。
    """

    _STATE_CODE = {"closed": 0, "half_open": 1, "open": 2}

    def __init__(
        self,
        key: str,
        failure_threshold: int,
        recovery_timeout: float,
        half_open_max_calls: int,
        half_open_success_rate: float,
    ) -> None:
        self.key = key  # "provider:model"
        self._failure_threshold = max(1, failure_threshold)
        # 下限 0.01：防 0/负值导致立即重开；生产配置 30s，测试可用小值
        self._recovery_timeout = max(0.01, recovery_timeout)
        self._half_open_max_calls = max(1, half_open_max_calls)
        self._half_open_success_rate = half_open_success_rate
        self._lock = asyncio.Lock()
        self._state = "closed"
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._half_calls = 0
        self._half_success = 0
        self._half_total = 0

    @property
    def state(self) -> str:
        return self._state

    async def acquire(self) -> bool:
        """请求进入前调用。False = 快速失败，调用方应立即走 fallback。"""
        async with self._lock:
            if self._state == "open":
                if time.monotonic() - self._opened_at >= self._recovery_timeout:
                    self._enter("half_open")  # 冷却结束，放行有限探测
                else:
                    return False
            if self._state == "half_open":
                if self._half_calls >= self._half_open_max_calls:
                    return False  # 探测名额已满
                self._half_calls += 1
            return True

    async def record_success(self) -> None:
        async with self._lock:
            if self._state == "half_open":
                self._half_success += 1
                self._half_total += 1
                if self._half_total >= 2 and (
                    self._half_success / self._half_total >= self._half_open_success_rate
                ):
                    self._enter("closed")  # 探测达标，恢复
            elif self._state == "closed":
                self._consecutive_failures = 0

    async def record_failure(self) -> None:
        async with self._lock:
            if self._state == "half_open":
                self._half_total += 1
                self._enter("open")  # 探测失败，重新打开
            elif self._state == "closed":
                self._consecutive_failures += 1
                if self._consecutive_failures >= self._failure_threshold:
                    self._enter("open")

    def _enter(self, state: str) -> None:
        self._state = state
        if state == "open":
            self._opened_at = time.monotonic()
            self._consecutive_failures = 0
            log.warning("[llm_breaker] {} OPEN（连续失败或探测失败）", self.key)
            _inc("llm_breaker_opens_total", circuit=self.key)
        elif state == "closed":
            log.info("[llm_breaker] {} CLOSED（探测恢复）", self.key)
        self._half_calls = self._half_success = self._half_total = 0
        _set_gauge("llm_circuit_state", self._STATE_CODE[state], circuit=self.key)


_breakers: Dict[str, CircuitBreaker] = {}


def get_breaker(model_name: str) -> CircuitBreaker:
    """取（或创建）模型级熔断器，键为 provider:model（模型故障是全局性的）。"""
    provider = config.get_provider_name_by_model_name(model_name)
    key = f"{provider}:{model_name}"
    breaker = _breakers.get(key)
    if breaker is None:
        cfg = config.llm
        breaker = CircuitBreaker(
            key,
            failure_threshold=cfg.breaker_failure_threshold,
            recovery_timeout=cfg.breaker_recovery_timeout_seconds,
            half_open_max_calls=cfg.breaker_half_open_max_calls,
            half_open_success_rate=cfg.breaker_half_open_success_rate,
        )
        _breakers[key] = breaker
    return breaker


def circuit_snapshot() -> Dict[str, str]:
    """全部熔断器状态快照（管理/排障用）。"""
    return {k: b.state for k, b in _breakers.items()}


# ── 内部工具 ───────────────────────────────────────────────────────────────

def _retry_after_seconds(exc: BaseException, cap: float) -> Optional[float]:
    """从 429 响应提取 Retry-After（秒），封顶 cap；取不到返回 None。"""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    raw = None
    if headers is not None:
        try:
            raw = headers.get("retry-after") or headers.get("Retry-After")
        except Exception:  # noqa: BLE001
            raw = None
    if raw:
        try:
            return min(float(raw), cap)
        except (TypeError, ValueError):
            pass
    return None


def _backoff_delay(attempt: int, exc: Optional[BaseException] = None) -> float:
    """指数退避 + 抖动；429 带合法 Retry-After 时取两者较大值（封顶）。"""
    cfg = config.llm
    base = min(cfg.backoff_base_seconds * (2 ** attempt), cfg.backoff_max_seconds)
    ra = _retry_after_seconds(exc, cfg.backoff_max_seconds) if exc is not None else None
    delay = max(base, ra) if ra is not None else base
    return delay + random.uniform(0, 0.25 * base)


def _log_call(scenario: str, model: str, attempt: int, latency_s: float,
              outcome: str, fallback_used: bool = False) -> None:
    # 结构化调用日志：只记元数据，不记消息内容
    log.debug(
        "[llm_call] scenario={} model={} attempt={} outcome={} fallback={} latency={:.2f}s",
        scenario, model, attempt, outcome, fallback_used, latency_s,
    )


def _record_outcome(scenario: str, model: str, cls: Optional[LLMErrorClass]) -> None:
    outcome = "ok" if cls is None else f"error_{cls.value}"
    _inc("llm_scenario_calls_total", scenario=scenario, model=model, outcome=outcome)


def _exec_kwargs(tools=None, max_tokens=None, temperature=None) -> Dict[str, Any]:
    """只透传显式给出的参数，其余交给 AsyncLLMClient 的默认值。"""
    kwargs: Dict[str, Any] = {}
    if tools is not None:
        kwargs["tools"] = tools
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if temperature is not None:
        kwargs["temperature"] = temperature
    return kwargs


# ── 非流式执行 ─────────────────────────────────────────────────────────────

async def resilient_invoke(
    scenario: str,
    messages: List[Dict[str, Any]],
    *,
    tools: Optional[List[Dict]] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    primary_model: Optional[str] = None,
    agent: Optional[str] = None,
    agent_override: Optional[Dict[str, Any]] = None,
) -> str:
    """非流式调用：同模型退避重试 → 逐级 fallback（每级独立熔断）。

    P4 §10.4：agent/agent_override 传智能体维度的模型覆盖（spec.name / spec.model_override）。
    """
    chain = config.get_model_chain(
        scenario, primary_override=primary_model,
        agent=agent, agent_override=agent_override,
    )
    kwargs = _exec_kwargs(tools, max_tokens, temperature)
    last_exc: Optional[BaseException] = None
    max_attempts = max(0, config.llm.max_attempts)

    for idx, model in enumerate(chain):
        fallback_used = idx > 0
        if fallback_used:
            _inc("llm_fallback_total", scenario=scenario, model=model)
        breaker = get_breaker(model)
        if not await breaker.acquire():
            log.warning("[llm] {} 熔断打开，跳过模型 {}（scenario={}）", chain[0], model, scenario)
            if last_exc is None:
                last_exc = LLMNoModelAvailableError(f"circuit open: {model}")
            continue

        for attempt in range(max_attempts + 1):
            try:
                t0 = time.monotonic()
                content = await get_llm(model).invoke(messages, **kwargs)
                await breaker.record_success()
                _record_outcome(scenario, model, None)
                _log_call(scenario, model, attempt, time.monotonic() - t0, "ok", fallback_used)
                return content
            except asyncio.CancelledError:
                raise  # 停止请求/任务取消：绝不重试
            except Exception as exc:
                cls = classify_llm_error(exc)
                _record_outcome(scenario, model, cls)
                _log_call(scenario, model, attempt, time.monotonic() - t0,
                          f"error_{cls.value}", fallback_used)
                last_exc = exc
                if cls is LLMErrorClass.RETRYABLE:
                    await breaker.record_failure()  # 只有上游健康类失败才计熔断
                if cls is LLMErrorClass.LOGIC:
                    raise  # 本地 bug：立即暴露
                if cls is not LLMErrorClass.RETRYABLE:
                    break  # OVERFLOW/FATAL：不重试，下一级模型
                if attempt >= max_attempts:
                    break  # 重试耗尽：下一级模型
                delay = _backoff_delay(attempt, exc)
                log.warning(
                    "[llm] scenario={} model={} 瞬态错误({})，{:.1f}s 后重试（{}/{}）: {}",
                    scenario, model, cls.value, delay, attempt + 1, max_attempts, exc,
                )
                _inc("llm_retry_total", scenario=scenario, model=model)
                await asyncio.sleep(delay)

    if last_exc is not None:
        raise last_exc
    raise LLMNoModelAvailableError(f"scenario={scenario} 无可用模型（链为空）")


# ── 流式执行 ───────────────────────────────────────────────────────────────

async def resilient_invoke_stream(
    scenario: str,
    messages: List[Dict[str, Any]],
    *,
    tools: Optional[List[Dict]] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    primary_model: Optional[str] = None,
    timeout: Optional[float] = None,
    agent: Optional[str] = None,
    agent_override: Optional[Dict[str, Any]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """流式调用：重试/换模型仅限**首 chunk 之前**；首 chunk 后失败直接抛。

    这是 P1-5（流式中断重试导致前端内容重复）的修复点：一旦有内容已透传给
    上层消费者，任何失败都不能透明重放，只能让本轮显式失败。
    P4 §10.4：agent/agent_override 传智能体维度的模型覆盖（spec.name / spec.model_override）。
    """
    chain = config.get_model_chain(
        scenario, primary_override=primary_model,
        agent=agent, agent_override=agent_override,
    )
    kwargs = _exec_kwargs(tools, max_tokens, temperature)
    if timeout is not None:
        kwargs["timeout"] = timeout
    last_exc: Optional[BaseException] = None
    max_attempts = max(0, config.llm.max_attempts)

    for idx, model in enumerate(chain):
        fallback_used = idx > 0
        if fallback_used:
            _inc("llm_fallback_total", scenario=scenario, model=model)
        breaker = get_breaker(model)
        if not await breaker.acquire():
            log.warning("[llm] {} 熔断打开，跳过模型 {}（scenario={}）", chain[0], model, scenario)
            if last_exc is None:
                last_exc = LLMNoModelAvailableError(f"circuit open: {model}")
            continue

        first_chunk_seen = False
        for attempt in range(max_attempts + 1):
            t0 = time.monotonic()
            try:
                async for item in get_llm(model).invoke_stream(messages, **kwargs):
                    first_chunk_seen = True
                    yield item
                await breaker.record_success()
                _record_outcome(scenario, model, None)
                _log_call(scenario, model, attempt, time.monotonic() - t0, "ok", fallback_used)
                return
            except asyncio.CancelledError:
                raise  # 用户停止/客户端断开：绝不重试、不计失败
            except Exception as exc:
                cls = classify_llm_error(exc)
                _record_outcome(scenario, model, cls)
                _log_call(scenario, model, attempt, time.monotonic() - t0,
                          f"error_{cls.value}", fallback_used)
                last_exc = exc
                if cls is LLMErrorClass.RETRYABLE:
                    await breaker.record_failure()
                if first_chunk_seen:
                    # 首 chunk 已透传：只能显式失败（透明重放会让用户看到重复内容）
                    log.warning("[llm] scenario={} 首 chunk 后失败({})，终止本轮: {}",
                                scenario, cls.value, exc)
                    raise
                if cls is LLMErrorClass.LOGIC:
                    raise
                if cls is not LLMErrorClass.RETRYABLE:
                    break
                if attempt >= max_attempts:
                    break
                delay = _backoff_delay(attempt, exc)
                log.warning(
                    "[llm] scenario={} model={} 流式瞬态错误({})，{:.1f}s 后重试（{}/{}）: {}",
                    scenario, model, cls.value, delay, attempt + 1, max_attempts, exc,
                )
                _inc("llm_retry_total", scenario=scenario, model=model)
                await asyncio.sleep(delay)

    if last_exc is not None:
        raise last_exc
    raise LLMNoModelAvailableError(f"scenario={scenario} 无可用模型（链为空）")
