"""Startup-only component selection and independent model profiles (stdlib only)."""
from copy import deepcopy
from dataclasses import dataclass, field
import os
from pathlib import Path
import tomllib


VERIFY_DEFAULTS = dict(backend="direct_agent", model_profile="verification", tool_protocol="native",
                       timeout_seconds=120, max_model_turns=8, max_tool_calls=24,
                       max_total_tokens=0, max_output_tokens_per_call=3000,
                       page_rows=30, max_tool_result_chars=12000, context_mode="compact",
                       dynamic_evidence_tools=False)
GUIDED_DEFAULTS = dict(VERIFY_DEFAULTS, backend="guided_agent", worker_concurrency=3)
PROFILE = dict(model_env="LLM_MODEL", base_url_env="LLM_BASE_URL", api_key_env="LLM_API_KEY",
               reasoning_effort="auto")
DEFAULTS = dict(schema_version=1,
                pipeline=dict(generator="current", verifier="legacy", verification_mode="gate"),
                generators=dict(current=dict(backend="current", model_profile="generation")),
                verifiers=dict(legacy=dict(backend="legacy"), direct_agent=VERIFY_DEFAULTS, guided_agent=GUIDED_DEFAULTS),
                models=dict(generation=dict(PROFILE), verification=dict(PROFILE)))


@dataclass(frozen=True)
class ModelSettings:
    model: str
    base_url: str = field(repr=False)
    api_key: str = field(repr=False)
    reasoning_effort: str = "auto"
    api_protocol: str = "responses"


def _merge(base, update):
    for key, value in update.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _merge(base[key], value)
        else:
            base[key] = deepcopy(value)
    return base


class Configuration:
    def __init__(self, value, environ=None, model_roles=("generator", "verifier")):
        self.value = value
        self.environ = dict(os.environ if environ is None else environ)
        self.pipeline = value["pipeline"]
        self.model_roles = model_roles
        self.validate()

    def validate(self):
        v, p = self.value, self.pipeline
        if v.get("schema_version") != 1 or type(v.get("schema_version")) is not int:
            raise ValueError("Unsupported config schema_version")
        if set(v) - set(DEFAULTS):
            raise ValueError("Unknown configuration section")
        if set(p) != {"generator", "verifier", "verification_mode"}:
            raise ValueError("Invalid pipeline configuration")
        for role, supported in (("generator", {"current"}), ("verifier", {"legacy", "direct_agent", "guided_agent"})):
            profiles = v[role + "s"]
            name = p[role]
            if name not in profiles or profiles[name].get("backend") not in supported:
                raise ValueError("Unknown or unsupported " + role)
        if p["verification_mode"] not in {"gate", "shadow"}:
            raise ValueError("Unknown verification_mode")
        if p["verification_mode"] == "shadow" and self.verifier["backend"] not in {"direct_agent","guided_agent"}:
            raise ValueError("shadow requires a model verifier")
        if set(self.generator) != {"backend", "model_profile"}:
            raise ValueError("Invalid generator settings")
        if self.verifier["backend"] in {"direct_agent","guided_agent"}:
            expected = GUIDED_DEFAULTS if self.verifier["backend"] == "guided_agent" else VERIFY_DEFAULTS
            if set(self.verifier) != set(expected) or self.verifier["tool_protocol"] != "native":
                raise ValueError("Invalid model verifier settings; native tools are required")
            if self.verifier["context_mode"] not in {"compact", "legacy"}:
                raise ValueError("context_mode must be compact or legacy")
            if type(self.verifier['dynamic_evidence_tools']) is not bool:
                raise ValueError('dynamic_evidence_tools must be a boolean')
            if type(self.verifier['max_total_tokens']) is not int or self.verifier['max_total_tokens']<0:
                raise ValueError('Expected nonnegative integer: max_total_tokens (0 disables the total token budget)')
            for key in expected.keys() - {"backend", "model_profile", "tool_protocol", "context_mode", "dynamic_evidence_tools", "max_total_tokens"}:
                if type(self.verifier[key]) is not int or self.verifier[key] <= 0:
                    raise ValueError("Expected positive integer: " + key)
        elif set(self.verifier) != {"backend"}:
            raise ValueError("Task1 legacy verifier does not use a model profile")
        for name in self.model_roles:
            role = getattr(self, name)
            if "model_profile" in role:
                self.model(role["model_profile"])

    @property
    def generator(self):
        return self.value["generators"][self.pipeline["generator"]]

    @property
    def verifier(self):
        return self.value["verifiers"][self.pipeline["verifier"]]

    def model(self, name):
        profile = self.value["models"].get(name)
        if not isinstance(profile, dict) or set(profile) - {"model", "model_env", "base_url_env", "api_key_env", "reasoning_effort", "api_protocol"}:
            raise ValueError("Invalid model profile: " + str(name))
        if ("model" in profile) == ("model_env" in profile):
            raise ValueError("Specify exactly one of model and model_env")
        def env(key):
            variable = profile.get(key)
            if not isinstance(variable, str) or not self.environ.get(variable, "").strip():
                raise ValueError("Missing configured environment variable for " + key)
            return self.environ[variable].strip()
        model = profile.get("model") if "model" in profile else env("model_env")
        effort = profile.get("reasoning_effort", "auto")
        if not isinstance(model, str) or not model.strip() or effort not in {"auto", "none", "low", "medium", "high", "max"}:
            raise ValueError("Invalid model or reasoning_effort")
        base = env("base_url_env")
        if not base.startswith(("https://", "http://")):
            raise ValueError("Model endpoint must be an HTTP URL")
        protocol=profile.get("api_protocol", "responses")
        if protocol not in {"responses", "deepseek_beta_strict"}:
            raise ValueError("Unsupported model api_protocol")
        if protocol=="deepseek_beta_strict":
            from deepseek_strict import beta_endpoint
            beta_endpoint(base)
            if name==self.generator.get("model_profile"):
                raise ValueError("deepseek_beta_strict is supported only for verification")
        return ModelSettings(model.strip(), base.rstrip("/"), env("api_key_env"), effort, protocol)

    def snapshot(self):
        # Profile values contain only env names/model names, never resolved credentials.
        return deepcopy(self.value)


def load_config(path=None, *, generator=None, verifier=None, verification_mode=None, environ=None, model_roles=("generator", "verifier")):
    value = deepcopy(DEFAULTS)
    env = os.environ if environ is None else environ
    for profile in value["models"].values():
        profile["reasoning_effort"] = env.get("LLM_REASONING_EFFORT", "auto") or "auto"
    if path:
        with Path(path).open("rb") as stream:
            supplied = tomllib.load(stream)
        # Explicit model replaces the default env reference, and vice versa.
        for name, profile in supplied.get("models", {}).items():
            if "model" in profile and "model_env" not in profile:
                value["models"].setdefault(name, {}).pop("model_env", None)
        _merge(value, supplied)
    for key, selection in (("generator", generator), ("verifier", verifier), ("verification_mode", verification_mode)):
        if selection is not None:
            value["pipeline"][key] = selection
    return Configuration(value, environ, model_roles)
