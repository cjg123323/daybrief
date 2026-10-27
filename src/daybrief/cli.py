"""daybrief CLI. run (수집 → 필터 → 요약 → digest JSON + MD + 바탕화면 HTML), render (HTML만 재생성)."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from daybrief import config as config_mod
from daybrief.digest import merge_digest
from daybrief.llm import make_llm
from daybrief.pipeline import filter as flt
from daybrief.render import dashboard, markdown
from daybrief.sources.base import get_source, registered
from daybrief.store import Store


def cmd_run(args: argparse.Namespace) -> int:
    cfg = config_mod.load(args.config)
    base = Path(cfg["_base_dir"])
    data_dir = base / cfg["data_dir"]
    store = Store(data_dir / "daybrief.sqlite")
    # Actions 러너는 UTC → 날짜가 하루 밀리지 않도록 설정 타임존 기준
    now = datetime.now(ZoneInfo(cfg["timezone"]))
    since = now - timedelta(days=cfg["lookback_days"])
    profile = config_mod.profile_text(cfg)
    models = cfg["llm"]["models"]

    items, failures = [], []
    for name, scfg in (cfg.get("sources") or {}).items():
        if not scfg.get("enabled") or (args.only and name not in args.only):
            continue
        src = get_source(name)
        if args.where != "all" and src.run_on != args.where:
            continue
        try:
            got = src.fetch(since, scfg)
            print(f"[{name}] {len(got)} items", file=sys.stderr)
            items += got
        except Exception as e:  # noqa: BLE001 — 원칙 7: 소스 하나 실패해도 계속
            failures.append({"source": name, "error": f"{type(e).__name__}: {e}"[:200]})
            print(f"[{name}] FAILED: {e}", file=sys.stderr)

    new = store.new_only(items, record=not args.dry_run) if not args.no_dedupe else items
    cand = flt.prefilter(new, cfg["filter"])
    print(f"new={len(new)} after_prefilter={len(cand)}", file=sys.stderr)
    if args.dry_run:
        # 상태(SQLite)·다이제스트를 쓰지 않는다. 키워드 튜닝용으로 1차 필터 결과만 보여준다.
        print("dry-run: LLM 판정·저장 없음. 1차 필터 통과 항목:", file=sys.stderr)
        for it in cand:
            print(f"  - [{it.source}] {it.title}  {it.url}", file=sys.stderr)
        return 0

    llm_failed = False
    try:
        llm = make_llm(cfg["llm"]["provider"])
        judgements = flt.judge(cand, llm, profile=profile, feedback=store.feedback_examples(),
                               model=models.get("cheap", "claude-haiku-5-5"))
    except Exception as e:  # noqa: BLE001 — 원칙 7: LLM이 죽어도 수집 실패를 브리핑에 띄우고 항목은 다음 실행으로 미룬다
        llm_failed = True
        judgements = []
        msg = f"{type(e).__name__}: {e}"
        if "credit balance" in msg:
            msg = "API 크레딧 소진 (console.anthropic.com → Plans & Billing)"
        failures.append({"source": "llm", "error": msg[:200]})
        n = store.forget_unjudged([it.id for it in cand])
        print(f"[llm] FAILED: {msg[:200]} — {n} items deferred to next run", file=sys.stderr)
    by_id = {it.id: it for it in cand}
    entries = []
    kept = sorted((j for j in judgements
                   if j.relevant and j.score >= flt.threshold_for(cfg["filter"], by_id[j.item_id].kind)),
                  key=lambda j: -j.score)
    n_sum = 0
    for j in judgements:
        store.save_judgement(j)
    for j in kept:
        it = by_id[j.item_id]
        entry = {"item": it.to_dict(), "judgement": asdict(j)}
        if (it.kind == "paper" and j.score >= cfg["filter"]["summarize_threshold"]
                and it.body and n_sum < cfg["filter"]["max_summaries"]):
            try:
                s = flt.summarize(it, llm, profile=profile, model=models.get("smart", "claude-sonnet-5-5"))
                store.save_summary(s)
                entry["summary"] = asdict(s)
                n_sum += 1
            except Exception as e:  # noqa: BLE001 — 요약 실패는 판정 결과를 버릴 이유가 아니다
                failures.append({"source": "llm-summary", "error": f"{type(e).__name__}: {e}"[:200]})
                n_sum = cfg["filter"]["max_summaries"]  # 같은 오류로 반복 호출하지 않음
        entries.append(entry)

    today = now.strftime("%Y-%m-%d")
    digest = {"date": today, "generated_at": now.isoformat(),
              "failures": failures, "entries": entries}
    out = data_dir / "digest"
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"{today}.json"
    if json_path.exists():  # 같은 날 두 번째 실행: 앞서 찾은 항목을 지우지 않는다
        digest = merge_digest(json.loads(json_path.read_text("utf-8")), digest,
                              rejudged=[j.item_id for j in judgements])
    json_path.write_text(json.dumps(digest, ensure_ascii=False, indent=2), "utf-8")
    (out / f"{today}.md").write_text(markdown.render(digest, library=cfg.get("library")), "utf-8")
    print(f"wrote {out / today}.{{json,md}} ({len(digest['entries'])} entries, {len(entries)} new)", file=sys.stderr)
    print(f"wrote {dashboard.write(cfg)}", file=sys.stderr)
    return 1 if llm_failed else 0  # 작업 스케줄러 '마지막 실행 결과'에 실패가 보이도록


def cmd_render(args: argparse.Namespace) -> int:
    """3단계: 저장된 digest JSON → dashboard/index.html만 다시 생성. 수집·LLM 없음 (스타일 수정 후, 로그온 직후)."""
    cfg = config_mod.load(args.config)
    print(f"wrote {dashboard.write(cfg)}", file=sys.stderr)
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    from daybrief.sources.openalex import resolve_journals

    for line in resolve_journals(Path(args.path), args.mailto):
        print(line)
    print("\n※ verify: true 항목은 이름이 맞는지 확인하고, SJR에서 Q1 여부를 확인한 뒤 verify 줄을 지우세요.")
    return 0


def cmd_init_profile(args: argparse.Namespace) -> int:
    """0단계: 로컬 근거 → profile.proposed.yaml (+ journals.proposed.yaml). 기존 profile.yaml은 건드리지 않음."""
    import yaml

    from daybrief.profiling import collect, synthesize

    roots = [Path(r).expanduser() for r in args.roots]
    out = Path(args.out_dir)
    # daybrief 자체를 다룬 세션(엔진 레포, private 설정 레포)은 연구 근거가 아님
    claude_exclude = [p for p in (collect.engine_root(), out.resolve()) if p]
    privacy: dict = {}
    if args.privacy:
        privacy = yaml.safe_load(Path(args.privacy).read_text(encoding="utf-8")) or {}
    signals, venues = collect.collect_all(
        roots,
        claude_root=Path("/nonexistent") if args.no_claude_logs else None,
        claude_exclude=claude_exclude,
        zotero_db=Path(args.zotero) if args.zotero else None,
        exclude=privacy.get("exclude") or [],
        redact_terms=[str(t) for t in privacy.get("redact") or []],
    )
    counts: dict[str, int] = {}
    for s in signals:
        counts[s.kind] = counts.get(s.kind, 0) + 1
    print(f"signals: {counts}  venues: {len(venues)}", file=sys.stderr)
    out.mkdir(parents=True, exist_ok=True)

    payload, _ = synthesize.build_payload(signals)
    if args.preview:
        f = out / "profile_evidence.preview.txt"
        f.write_text(payload, "utf-8")
        print(f"LLM에 보낼 내용(마스킹 후)을 {f} 에 저장했습니다. 확인 후 --preview 없이 다시 실행하세요.")
        return 0
    if not signals:
        print("근거가 하나도 없습니다. --roots 경로를 확인하세요.", file=sys.stderr)
        return 1

    llm = make_llm(args.provider)
    data = synthesize.synthesize(signals, llm, model=args.model)
    keywords = data.pop("include_keywords", [])
    proposed = {"research": data, "filter": {"include_keywords": keywords}}
    header = ("# init-profile 생성 결과 (제안). 각 주제의 evidence가 실제로 맞는지 확인하고,\n"
              "# uncertain 항목에 답한 뒤 profile.yaml의 research / filter.include_keywords로 옮기세요.\n")
    (out / "profile.proposed.yaml").write_text(
        header + yaml.safe_dump(proposed, allow_unicode=True, sort_keys=False), "utf-8")
    if venues:
        (out / "journals.proposed.yaml").write_text(
            yaml.safe_dump(synthesize.journal_candidates(venues), allow_unicode=True, sort_keys=False), "utf-8")
    print(f"wrote {out / 'profile.proposed.yaml'}" + (" + journals.proposed.yaml" if venues else ""))
    return 0


def cmd_init_journals(args: argparse.Namespace) -> int:
    """0단계 보조: 로컬 PDF → OpenAlex → journals.proposed.yaml. LLM 호출 없음, 네트워크는 OpenAlex만."""
    import yaml

    from daybrief.profiling import journals

    roots = [Path(r).expanduser() for r in args.roots]
    privacy: dict = {}
    if args.privacy:
        privacy = yaml.safe_load(Path(args.privacy).read_text(encoding="utf-8")) or {}
    found, missed = journals.scan(roots, exclude=privacy.get("exclude") or [], mailto=args.mailto)
    print(f"resolved {len(found)} pdfs, unresolved {len(missed)}", file=sys.stderr)
    for m in missed:
        print(f"  unresolved: {m}", file=sys.stderr)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    data = journals.propose(found)
    (out / "journals.proposed.yaml").write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), "utf-8")
    print(f"wrote {out / 'journals.proposed.yaml'} ({len(data['journals'])} venues)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="daybrief")
    sub = p.add_subparsers(dest="cmd", required=True)
    ip = sub.add_parser("init-profile", help="0단계: 로컬 파일 근거로 연구 프로필 제안 생성")
    ip.add_argument("--roots", nargs="+", required=True, help="연구 폴더들 (git 레포, 논문 PDF, .bib가 있는 곳)")
    ip.add_argument("--out-dir", default=".")
    ip.add_argument("--zotero", help="zotero.sqlite 경로 (기본: ~/Zotero/zotero.sqlite)")
    ip.add_argument("--no-claude-logs", action="store_true", help="Claude Code 세션 로그 제외")
    ip.add_argument("--privacy", help="제외 경로(exclude glob)·마스킹 이름(redact) YAML. 개인 레포에 둘 것")
    ip.add_argument("--preview", action="store_true", help="LLM 호출 없이 전송될 내용만 파일로 저장")
    ip.add_argument("--provider", default="anthropic")
    ip.add_argument("--model", default="claude-sonnet-5-5")
    ip.set_defaults(func=cmd_init_profile)
    ij = sub.add_parser("init-journals",
                        help="0단계 보조: 로컬 PDF를 OpenAlex로 조회해 저널 후보 생성 (Zotero 없을 때)")
    ij.add_argument("--roots", nargs="+", required=True)
    ij.add_argument("--out-dir", default=".")
    ij.add_argument("--privacy", help="init-profile과 같은 privacy YAML (exclude만 적용)")
    ij.add_argument("--mailto", help="OpenAlex polite pool용 메일 (선택)")
    ij.set_defaults(func=cmd_init_journals)
    r = sub.add_parser("run", help="수집 → 필터 → 요약 → digest")
    r.add_argument("--config", required=True)
    r.add_argument("--only", nargs="*", help="특정 소스만")
    r.add_argument("--where", choices=["all", "cloud", "local"], default="all")
    r.add_argument("--dry-run", action="store_true", help="LLM 호출 없이")
    r.add_argument("--no-dedupe", action="store_true", help="이미 본 항목도 다시 처리")
    r.set_defaults(func=cmd_run)
    rd = sub.add_parser("render", help="저장된 digest JSON으로 바탕화면 HTML만 재생성 (LLM·네트워크 없음)")
    rd.add_argument("--config", required=True)
    rd.set_defaults(func=cmd_render)
    jr = sub.add_parser("resolve-journals", help="journals.yaml의 빈 ISSN을 OpenAlex로 채움")
    jr.add_argument("path")
    jr.add_argument("--mailto")
    jr.set_defaults(func=cmd_resolve)
    s = sub.add_parser("sources", help="등록된 소스 목록")
    s.set_defaults(func=lambda a: print("\n".join(registered())) or 0)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
