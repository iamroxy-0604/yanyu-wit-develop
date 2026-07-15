"""`wit provider` — 管理 LLM Provider 配置。

支持列表、新增、删除、切换激活的 Provider。
"""

from __future__ import annotations

import argparse
import sys

from cli.config import (
    list_providers,
    add_provider,
    remove_provider,
    get_active_provider_index,
    set_active_provider,
    mask_api_key,
)


# ---------------------------------------------------------------------------
# Type-specific defaults (shared with init_cmd)
# ---------------------------------------------------------------------------

PROVIDER_TYPE_DEFAULTS = {
    "openai": {"model": "gpt-4o", "base_url": "https://api.openai.com/v1", "need_key": True},
    "anthropic": {"model": "claude-sonnet-4-20250514", "base_url": "", "need_key": True},
    "google": {"model": "gemini-2.5-flash", "base_url": "", "need_key": True},
    "ollama": {"model": "qwen3:32b", "base_url": "http://localhost:11434/v1", "need_key": False},
}

VALID_TYPES = tuple(PROVIDER_TYPE_DEFAULTS.keys())


def register_parser(subparsers: argparse._SubParsersAction) -> None:
    """注册 `provider` 子命令及其子子命令。"""
    provider_parser = subparsers.add_parser(
        "provider",
        help="管理 LLM Provider 配置（list / add / remove / use）",
    )
    provider_sub = provider_parser.add_subparsers(dest="provider_action", help="Provider 操作")

    # provider list
    provider_sub.add_parser("list", help="列出所有已配置的 Provider")

    # provider add
    provider_sub.add_parser("add", help="交互式添加新 Provider")

    # provider remove <index>
    remove_parser = provider_sub.add_parser("remove", help="删除指定索引的 Provider")
    remove_parser.add_argument("index", type=int, help="要删除的 Provider 索引（0-based）")

    # provider use <index>
    use_parser = provider_sub.add_parser("use", help="切换当前激活的 Provider")
    use_parser.add_argument("index", type=int, help="要激活的 Provider 索引（0-based）")

    provider_parser.set_defaults(func=execute)


def execute(args: argparse.Namespace) -> None:
    """分发 provider 子命令。"""
    action = getattr(args, "provider_action", None)
    if action is None:
        print("💡 请指定操作: list / add / remove / use")
        print("   示例: wit provider list")
        return

    dispatch = {
        "list": _cmd_list,
        "add": _cmd_add,
        "remove": _cmd_remove,
        "use": _cmd_use,
    }
    handler = dispatch.get(action)
    if handler:
        handler(args)
    else:
        print(f"❌ 未知的 provider 操作: {action}")


def _cmd_list(_args: argparse.Namespace) -> None:
    """列出所有已配置的 Provider。"""
    providers = list_providers()
    active_idx = get_active_provider_index()

    if not providers:
        print("📋 尚未配置任何 Provider")
        print("   运行 `wit provider add` 添加一个")
        return

    print(f"\n📋 已配置的 Provider 列表（共 {len(providers)} 个）：\n")
    print(f"  {'':2s} {'#':>3s}  {'Type':<12s} {'Model':<25s} {'Base URL':<40s} {'API Key'}")
    print(f"  {'':2s} {'─'*3}  {'─'*12} {'─'*25} {'─'*40} {'─'*20}")

    for i, p in enumerate(providers):
        marker = " ✅" if i == active_idx else "   "
        ptype = p.get("type", "?")
        name = p.get("name", "?")
        base_url = p.get("base_url", "") or "(默认)"
        api_key = mask_api_key(p.get("api_key", ""))
        print(f"  {marker} {i:>3d}  {ptype:<12s} {name:<25s} {base_url:<40s} {api_key}")

    print(f"\n  当前激活: #{active_idx}")
    print()


def _cmd_add(_args: argparse.Namespace) -> None:
    """交互式添加新 Provider。"""
    print("\n➕ 添加新的 LLM Provider\n")

    # Type selection
    provider_type = ""
    while provider_type not in VALID_TYPES:
        provider_type = input(f"  Provider 类型 [{'/'.join(VALID_TYPES)}]: ").strip().lower()
        if provider_type not in VALID_TYPES:
            print(f"  ⚠️ 不支持的类型，请选择: {', '.join(VALID_TYPES)}")

    defaults = PROVIDER_TYPE_DEFAULTS[provider_type]

    model_name = input(f"  Model name [{defaults['model']}]: ").strip() or defaults["model"]
    base_url = input(f"  API Base URL [{defaults['base_url'] or '(默认)'}]: ").strip() or defaults["base_url"]

    if defaults["need_key"]:
        api_key = input("  API Key: ").strip()
        if not api_key:
            print("  ⚠️ 未输入 API Key，provider 可能无法正常工作")
    else:
        api_key = input("  API Key [ollama]: ").strip() or "ollama"

    try:
        idx = add_provider({
            "type": provider_type,
            "name": model_name,
            "base_url": base_url,
            "api_key": api_key,
        })
        print(f"\n  ✅ 已添加 Provider #{idx}: {provider_type} / {model_name}")

        # Ask if user wants to activate it
        providers = list_providers()
        if len(providers) > 1:
            activate = input(f"  是否立即激活此 Provider？[Y/n]: ").strip().lower()
            if activate != "n":
                set_active_provider(idx)
                print(f"  ✅ 已切换到 Provider #{idx}")
        print()
    except Exception as e:
        print(f"  ❌ 添加失败: {e}")
        sys.exit(1)


def _cmd_remove(args: argparse.Namespace) -> None:
    """删除指定索引的 Provider。"""
    index = args.index
    providers = list_providers()
    active_idx = get_active_provider_index()

    if not providers or index < 0 or index >= len(providers):
        print(f"❌ 无效的索引: {index}（当前共有 {len(providers)} 个 Provider）")
        sys.exit(1)

    p = providers[index]
    ptype = p.get("type", "?")
    name = p.get("name", "?")

    if len(providers) == 1:
        print(f"⚠️ 这是唯一的 Provider，删除后将无法使用模型功能")
        confirm = input(f"  确定删除 #{index} ({ptype}/{name})？[y/N]: ").strip().lower()
        if confirm != "y":
            print("  已取消")
            return

    if remove_provider(index):
        print(f"✅ 已删除 Provider #{index}: {ptype} / {name}")
        if index == active_idx:
            print(f"   已自动切换到 Provider #0")
    else:
        print(f"❌ 删除失败")
        sys.exit(1)


def _cmd_use(args: argparse.Namespace) -> None:
    """切换当前激活的 Provider。"""
    index = args.index
    providers = list_providers()

    if not providers or index < 0 or index >= len(providers):
        print(f"❌ 无效的索引: {index}（当前共有 {len(providers)} 个 Provider）")
        sys.exit(1)

    try:
        set_active_provider(index)
        p = providers[index]
        print(f"✅ 已切换到 Provider #{index}: {p.get('type', '?')} / {p.get('name', '?')}")
    except Exception as e:
        print(f"❌ 切换失败: {e}")
        sys.exit(1)
