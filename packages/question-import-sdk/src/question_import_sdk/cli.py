"""Chinese CLI intended to be driven by the import Skill or an administrator."""

import argparse
import getpass
import json
import sys
from pathlib import Path

import httpx
from question_content_contracts.validation import load_json

from .client import ApiError, SignedClient
from .config import default_home, load_config, save_config
from .ledger import uuid7
from .workflow import ImportWorkflow

DEFAULT_HOME = default_home()


def output(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def _main(*, client_factory=None, tool_version=None):
    parser = argparse.ArgumentParser(description="雅思／托福本地校验、自动媒体及直接发布工具")
    if tool_version:
        parser.add_argument("--version", action="version", version=tool_version)
    parser.add_argument(
        "--profile",
        type=Path,
        default=DEFAULT_HOME / "profile.json",
        help="本机600权限凭据文件，不放入批次或聊天",
    )
    parser.add_argument("--cache", type=Path, default=DEFAULT_HOME / "contracts")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("configure", help="隐藏输入AK/SK，安全绑定服务地址")
    sub.add_parser("doctor", help="检查运行时、身份、环境和权限")
    ids = sub.add_parser("ids", help="预分配稳定UUIDv7")
    ids.add_argument("--count", type=int, default=1)
    init = sub.add_parser("prepare", help="从Agent整理的候选文件建立持久批次")
    init.add_argument("candidate", type=Path)
    init.add_argument("--batch", required=True, type=Path)
    for name in ("validate", "run", "status", "query", "preview", "revise", "confirm"):
        cmd = sub.add_parser(name)
        cmd.add_argument("batch", type=Path)
        if name == "run":
            cmd.add_argument(
                "--retry-operation",
                action="append",
                default=[],
                help="仅在用户已选择重试时指定content或media:UUID",
            )
        if name == "confirm":
            cmd.add_argument("--preview", required=True, type=Path)
        if name == "preview":
            cmd.add_argument("--output", type=Path, help="保存到当前会话目录的预览网页")
        if name == "revise":
            cmd.add_argument("candidate", type=Path)
    replace = sub.add_parser("replace-media", help="修正失败媒体并创建新逻辑操作，保留已发布内容")
    replace.add_argument("batch", type=Path)
    replace.add_argument("reference")
    replace.add_argument("candidate", type=Path)
    contract = sub.add_parser("contract")
    contract.add_argument("exam", choices=["ielts", "toefl"])
    contract.add_argument("--version")
    read = sub.add_parser("content")
    read.add_argument("kind", choices=["ielts_question", "ielts_paper", "toefl_item", "toefl_form"])
    read.add_argument("--id")
    read.add_argument("--q")
    read.add_argument("--status")
    read.add_argument("--page", type=int, default=1)
    args = parser.parse_args()
    if args.command == "ids":
        if not 1 <= args.count <= 1000:
            parser.error("count必须为1–1000")
        output([uuid7() for _ in range(args.count)])
        return
    if args.command == "configure":
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ValueError("请由用户本人在本机交互终端运行configure；非TTY环境禁止输入AK/SK")
        origin = input("服务地址（HTTPS；本地开发可用 http://127.0.0.1:23100）：").strip()
        access_key = getpass.getpass("AK（隐藏输入）：").strip()
        secret_key = getpass.getpass("SK（隐藏输入，不粘贴到聊天）：").strip()
        save_config(args.profile, origin, access_key, secret_key)
        print("配置已以600权限保存。执行 doctor 检查身份和权限。")
        return
    profile = load_config(args.profile)
    client = client_factory(profile, args.profile) if client_factory else SignedClient(profile)
    workflow = ImportWorkflow(client, args.cache)
    try:
        if args.command == "doctor":
            import shutil

            me = workflow.identify()
            output(
                {
                    "python": sys.version.split()[0],
                    "ffprobe_available": bool(shutil.which("ffprobe")),
                    "origin": profile.origin,
                    "identity": me,
                }
            )
            return
        if args.command == "contract":
            output(workflow.cache.pin(profile.origin, client.contract(args.exam, args.version)))
            return
        if args.command == "content":
            query = {
                k: v
                for k, v in {
                    "q": args.q,
                    "status": args.status,
                    "page": args.page,
                }.items()
                if v is not None
            }
            output(client.content(args.kind, args.id, **query))
            return
        if args.command == "prepare":
            candidate = load_json(args.candidate.read_bytes())
            media = candidate.get("media", [])
            for entry in media:
                source = Path(entry["path"]).expanduser()
                if not source.is_absolute():
                    entry["path"] = str((args.candidate.parent / source).resolve())
            batch = workflow.create_batch(
                args.batch,
                candidate["exam"],
                candidate["objects"],
                candidate.get("source_manifest", []),
                media=media,
                config_ref=str(args.profile.resolve()),
                dependencies=candidate.get("dependencies"),
            )
            output(workflow.report(batch))
            return
        batch = workflow.open_batch(args.batch)
        if args.command == "run":
            output(workflow.run(batch, retry_operations=args.retry_operation))
        elif args.command == "query":
            output(workflow.query(batch))
        elif args.command == "status":
            output(workflow.report(batch))
        elif args.command == "validate":
            workflow._dependencies(batch)
            output({"issues": workflow.validate(batch)})
        elif args.command == "replace-media":
            candidate = load_json(args.candidate.read_bytes())
            source = Path(candidate["path"]).expanduser()
            if not source.is_absolute():
                candidate["path"] = str((args.candidate.parent / source).resolve())
            workflow.replace_media(batch, args.reference, candidate)
            output(workflow.report(batch))
        elif args.command == "revise":
            candidate = load_json(args.candidate.read_bytes())
            for entry in candidate.get("media", []):
                source = Path(entry["path"]).expanduser()
                if not source.is_absolute():
                    entry["path"] = str((args.candidate.parent / source).resolve())
            workflow.revise_batch(batch, candidate)
            output(workflow.report(batch))
        elif args.command == "confirm":
            output(workflow.confirm(batch, args.preview))
        elif args.command == "preview":
            from .preview import export_preview_for_open

            saved, opened = export_preview_for_open(
                batch,
                args.output or batch.root / "试题预览.html",
                client=client,
            )
            output({"preview_file": str(saved), "preview_open_file": str(opened)})
    finally:
        client.close()


def main(*, client_factory=None, tool_version=None):
    try:
        _main(client_factory=client_factory, tool_version=tool_version)
    except ApiError as error:
        output({"error": error.public()})
        raise SystemExit(2) from None
    except httpx.TransportError:
        output(
            {
                "error": {
                    "code": "connection_failed",
                    "message": "网络连接失败，已保留本地资料；写操作结果请按原账本查询。",
                }
            }
        )
        raise SystemExit(2) from None
    except (ValueError, OSError, KeyError) as error:
        output({"error": {"code": "local_error", "message": str(error)}})
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
