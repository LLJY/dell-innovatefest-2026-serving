from __future__ import annotations

from pathlib import Path
import os
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_litellm_routes_omnilion_to_authenticated_host_gateway() -> None:
    config = yaml.safe_load((ROOT / "litellm-config.yaml").read_text())
    models = {entry["model_name"]: entry["litellm_params"] for entry in config["model_list"]}
    assert models["gpt-5.6-sol"] == {
        "model": "openai/gpt-5.6-sol",
        "api_base": "http://translator:8787/v1",
        "api_key": "os.environ/TRANSLATOR_API_KEY",
    }
    assert models["OmniLion"] == {
        "model": "openai/OmniLion",
        "api_base": "os.environ/OMNILION_API_BASE",
        "api_key": "os.environ/OMNILION_API_KEY",
    }

    compose = yaml.safe_load((ROOT / "compose.luna.yml").read_text())
    litellm = compose["services"]["litellm"]
    assert litellm["environment"]["OMNILION_API_BASE"] == "${OMNILION_API_BASE}"
    assert litellm["environment"]["OMNILION_API_KEY"] == "${OMNILION_API_KEY}"
    assert "host.docker.internal:host-gateway" in litellm["extra_hosts"]


def test_omnilion_service_uses_user_systemd_and_full_modalities() -> None:
    script = (ROOT / "scripts" / "omnilion-service.sh").read_text()
    assert "systemd-run --user" in script
    assert "--limit-mm-per-prompt" in script
    assert "{\"image\":1,\"video\":1,\"audio\":1}" in script
    assert "--media-io-kwargs" in script
    assert '\\"video\\":{\\"num_frames\\":${OMNILION_VIDEO_NUM_FRAMES}}' in script
    assert "--api-key" not in script
    assert '--property="EnvironmentFile=$ENV_FILE"' in script
    assert 'export VLLM_API_KEY="$OMNILION_API_KEY"' in script
    assert '--setenv "PATH=$OMNILION_VENV/bin:$PATH"' in script
    assert '--host "$OMNILION_BIND_HOST"' in script
    assert "docker network inspect bridge" in script
    assert 'OMNILION_BIND_HOST" == "$bridge_host"' in script
    assert 'http://host.docker.internal:${OMNILION_PORT}/v1' in script
    assert 'systemctl --user stop "$unit"' in script
    assert "systemctl --user --no-pager --full status" not in script
    assert "Application startup" not in script  # readiness is HTTP, not log matching


def test_luna_start_orders_backend_before_litellm() -> None:
    script = (ROOT / "scripts" / "luna-up.sh").read_text()
    translator = script.index("compose up -d postgres translator")
    omnilion = script.index('"$SCRIPT_DIR/omnilion-service.sh" start')
    litellm = script.index("compose up -d litellm")
    assert translator < omnilion < litellm


def test_standalone_omnilion_stack_needs_only_vllm_and_litellm() -> None:
    script = (ROOT / "scripts" / "omnilion-stack.sh").read_text()
    pull = script.index("snapshot_download")
    install = script.index("-m pip install --no-deps --force-reinstall")
    vllm = script.index('"$SCRIPT_DIR/omnilion-service.sh" start')
    litellm = script.index("docker run -d")
    assert pull < install < vllm < litellm
    assert "translator" not in script
    assert "postgres" not in script.lower()
    assert "native_was_active=false" in script
    assert '[[ "$native_was_active" == true ]] ||' in script

    service = (ROOT / "scripts" / "omnilion-service.sh").read_text()
    assert '--revision "$OMNILION_REVISION"' in service

    env = (ROOT / ".env.example").read_text()
    assert "OMNILION_MODEL=LLJYY/OmniLion" in env
    assert "OMNILION_REVISION=405ef8e1d21224edca0bdff6499389d4260c17e2" in env

    config = yaml.safe_load((ROOT / "litellm-omnilion.yaml").read_text())
    assert [row["model_name"] for row in config["model_list"]] == ["OmniLion"]
    assert "database_url" not in config["general_settings"]


def test_luna_start_rolls_back_and_combined_shutdown_stops_both_layers() -> None:
    up = (ROOT / "scripts" / "luna-up.sh").read_text()
    assert up.count("compose down --remove-orphans") >= 3
    assert '"$SCRIPT_DIR/omnilion-service.sh" stop' in up

    down = (ROOT / "scripts" / "luna-down.sh").read_text()
    assert "compose down --remove-orphans" in down
    assert '"$SCRIPT_DIR/omnilion-service.sh" stop' in down
    assert "named data volumes were preserved" in down


def test_deployment_key_authorizes_both_active_models() -> None:
    script = (ROOT / "scripts" / "luna-create-key.sh").read_text()
    assert '\\"models\\":[\\"gpt-5.6-sol\\",\\"OmniLion\\"]' in script


def test_example_environment_has_complete_omnilion_contract() -> None:
    text = (ROOT / ".env.example").read_text()
    required = {
        "OMNILION_MODEL",
        "OMNILION_REVISION",
        "OMNILION_VENV",
        "OMNILION_API_KEY",
        "OMNILION_API_BASE",
        "OMNILION_BIND_HOST",
        "OMNILION_PORT",
        "OMNILION_MAX_MODEL_LEN",
        "OMNILION_GPU_MEMORY_UTILIZATION",
        "OMNILION_VIDEO_NUM_FRAMES",
    }
    names = {
        line.split("=", 1)[0]
        for line in text.splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    assert required <= names


def test_common_permission_helpers_work_on_gnu_stat(tmp_path: Path) -> None:
    target = tmp_path / "secret.env"
    target.write_text("test=true\n")
    os.chmod(target, 0o600)
    command = r'''
source "$1"
printf '%s %s %s\n' "$(file_mode "$2")" "$(file_uid "$2")" "$(file_gid "$2")"
'''
    result = subprocess.run(
        ["bash", "-c", command, "bash", str(ROOT / "scripts" / "luna-common.sh"), str(target)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == f"600 {os.getuid()} {os.getgid()}"


def test_operations_document_active_omnilion_commands() -> None:
    text = (ROOT / "docs" / "luna-operations.md").read_text()
    assert "scripts/omnilion-service.sh status" in text
    assert "scripts/omnilion-stack.sh up" in text
    assert "journalctl --user -u omnilion-vllm" in text
    assert "model=\"OmniLion\"" in text
