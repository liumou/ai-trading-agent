"""laya 领域微调（Phase 3-A Step 3A.2，CPU 可行，零新依赖）

数据：laya_finetune_data.py 产出的 train/val.jsonl（合成确定性标签）。
模型：convaiinnovations/laya（ModernBERT-large + 2 层决策头），本地缓存加载。
策略：
  - 默认 head-only（encoder 冻结，forward detach_encoder=True）——CPU 上代价可控；
  - --unfreeze-last N：解冻 encoder 末 N 层（慢，谨慎使用）；
  - 损失：候选 logits 上的 CrossEntropy（collate_items 官方 label 通道）。
产物：backend/models/laya_ft/（model.safetensors + rl_agent_config.json + tokenizer/ + encoder/，
      可被 laya.load(path) 直接加载做推理/评估）。

用法（backend/ 下）：
  PYTHONPATH=. /tmp/laya-api-venv/bin/python scripts/laya_finetune.py \
      [--max-train 300] [--epochs 2] [--batch-size 8] [--lr 1e-4] \
      [--unfreeze-last 0] [--smoke 20] [--out models/laya_ft]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

import torch
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

DATA_DIR = ROOT / "models" / "laya_ft_data"
DEFAULT_OUT = ROOT / "models" / "laya_ft"

QTYPES = {"choice": 0, "score": 1, "noul": 2}


def load_items(path: Path, tok, questions: dict, max_train: int = 0) -> list[dict]:
    """JSONL → 每行 6 个训练 item（ids/markers/qtype/label），顺序与 criteria 键一致。"""
    from laya.agent import Agent
    from laya.common import build_sequence

    internal = {qid: Agent._to_internal(q) for qid, q in questions.items()}
    label_idx = {qid: list(q.get("criteria", {}).keys()) for qid, q in questions.items()}
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    if max_train:
        rows = rows[:max_train]
    out = []
    for r in rows:
        text = r["text"]
        labels = r["labels"]
        for qid, iq in internal.items():
            seq, markers = build_sequence(tok, text, iq, max_len=512, head_max_len=192)
            if len(markers) != len(label_idx[qid]):
                raise ValueError(f"markers != options for {qid}")
            lab = labels[qid]
            if lab not in label_idx[qid]:
                raise ValueError(f"unknown label {lab!r} for {qid}")
            out.append({
                "ids": seq, "markers": markers, "qtype": QTYPES[iq["t"]],
                "label": label_idx[qid].index(lab), "qid": qid,
            })
    return out


def make_batches(items: list[dict], rows_per_batch: int, rng: random.Random):
    """按行分组（每行 6 个 item）再拼 batch，保持 collate_items 的 group 语义。"""
    by_row = [items[i:i + 6] for i in range(0, len(items), 6)]
    rng.shuffle(by_row)
    for i in range(0, len(by_row), rows_per_batch):
        yield by_row[i:i + rows_per_batch]


def evaluate(model, tok, items, questions, batch_rows: int = 16) -> dict:
    from laya.common import collate_items

    model.eval()
    rng = random.Random(0)
    total_loss = 0.0
    total = 0.0
    correct = 0.0
    per_q = {qid: [0, 0] for qid in questions}  # [correct, total]
    with torch.no_grad():
        for group in make_batches(items, batch_rows, rng):
            b = collate_items(group, tok.pad_token_id)
            if b is None:
                continue
            logits, _ = model(
                b["input_ids"], b["attention_mask"], b["marker_pos"],
                b["marker_mask"], b["qtype"], detach_encoder=True,
            )
            mask = b["marker_mask"]
            item_losses = []
            for i, it in enumerate(b["meta"]):
                k = int(mask[i].sum())
                item_losses.append(F.cross_entropy(logits[i, :k], b["label"][i].long()))
                pred = int(logits[i, :k].argmax(-1))
                correct += int(pred == b["label"][i])
                qid = it["qid"]
                per_q[qid][1] += 1
                per_q[qid][0] += int(pred == b["label"][i])
            total += len(b["meta"])
            total_loss += float(torch.stack(item_losses).mean()) * len(b["meta"])
    return {
        "loss": total_loss / total if total else float("nan"),
        "acc": correct / total if total else float("nan"),
        "per_q": {qid: (c / n if n else float("nan")) for qid, (c, n) in per_q.items()},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--max-train", type=int, default=0)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--unfreeze-last", type=int, default=0)
    ap.add_argument("--smoke", type=int, default=0, help="只跑 N 步校准步时")
    ap.add_argument("--val-max", type=int, default=200, help="val 评估子集行数（加速 CPU 评估）")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    import laya
    from huggingface_hub import snapshot_download
    from laya.agent import Agent
    from laya.common import collate_items

    from app.ai.laya_gate import LAYA_GATE_QUESTIONS

    cache = Path(snapshot_download("convaiinnovations/laya", local_files_only=True))
    print("[model] loading base (local cache)...")
    agent = laya.load(cache, device="cpu")
    model = agent.model
    tok = agent.tok
    print(f"[model] params: encoder={sum(p.numel() for p in model.encoder.parameters()):,} "
          f"head+scorer={sum(p.numel() for p in list(model.head.parameters()) + list(model.scorer.parameters())):,}")

    # 冻结策略
    for p in model.encoder.parameters():
        p.requires_grad = False
    if args.unfreeze_last > 0:
        layers = list(model.encoder.layers)
        for layer in layers[-args.unfreeze_last:]:
            for p in layer.parameters():
                p.requires_grad = True
        print(f"[model] unfrozen last {args.unfreeze_last} encoder layers")
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"[model] trainable params: {trainable:,}")

    train_items = load_items(args.data_dir / "train.jsonl", tok, LAYA_GATE_QUESTIONS, args.max_train)
    val_items = load_items(args.data_dir / "val.jsonl", tok, LAYA_GATE_QUESTIONS)
    if args.val_max:
        val_items = val_items[: args.val_max * 6]
    print(f"[data] train rows={len(train_items) // 6} items={len(train_items)}  "
          f"val rows={len(val_items) // 6} items={len(val_items)}")

    if args.smoke:
        print(f"[smoke] {args.smoke} steps timing...")
        started = time.perf_counter()
        opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=args.lr)
        model.train()
        rng = random.Random(args.seed)
        for step, group in enumerate(make_batches(train_items, args.batch_size, rng)):
            if step >= args.smoke:
                break
            b = collate_items(group, tok.pad_token_id)
            logits, _ = model(b["input_ids"], b["attention_mask"], b["marker_pos"],
                              b["marker_mask"], b["qtype"], detach_encoder=(args.unfreeze_last == 0))
            mask = b["marker_mask"]
            mask = b["marker_mask"]
            item_losses = []
            for i in range(len(b["meta"])):
                k = int(mask[i].sum())
                item_losses.append(F.cross_entropy(logits[i, :k], b["label"][i].long()))
            loss = torch.stack(item_losses).mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
        elapsed = time.perf_counter() - started
        print(f"[smoke] {args.smoke} steps in {elapsed:.1f}s → {elapsed / args.smoke:.2f}s/step; "
              f"epoch estimate (rows={len(train_items) // 6}, bs={args.batch_size}): "
              f"{elapsed / args.smoke * math.ceil((len(train_items) // 6) / args.batch_size) / 60:.1f} min")
        return

    # 基线与训练
    base_eval = evaluate(model, tok, val_items, LAYA_GATE_QUESTIONS)
    print(f"[eval] base val loss={base_eval['loss']:.4f} acc={base_eval['acc']:.4f}")
    print(f"[eval] base per-q: { {k: round(v, 3) for k, v in base_eval['per_q'].items()} }")

    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=args.lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)
    steps_per_epoch = math.ceil((len(train_items) // 6) / args.batch_size)
    print(f"[train] {args.epochs} epochs × {steps_per_epoch} steps")

    for epoch in range(1, args.epochs + 1):
        model.train()
        rng = random.Random(args.seed + epoch)
        t0 = time.perf_counter()
        tot_loss = 0.0
        tot_n = 0
        for step, group in enumerate(make_batches(train_items, args.batch_size, rng), 1):
            b = collate_items(group, tok.pad_token_id)
            logits, _ = model(b["input_ids"], b["attention_mask"], b["marker_pos"],
                              b["marker_mask"], b["qtype"], detach_encoder=(args.unfreeze_last == 0))
            mask = b["marker_mask"]
            mask = b["marker_mask"]
            item_losses = []
            for i in range(len(b["meta"])):
                k = int(mask[i].sum())
                item_losses.append(F.cross_entropy(logits[i, :k], b["label"][i].long()))
            loss = torch.stack(item_losses).mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot_loss += float(loss) * len(b["meta"])
            tot_n += len(b["meta"])
            if step % 50 == 0:
                print(f"  epoch {epoch} step {step}/{steps_per_epoch} loss={tot_loss / tot_n:.4f} "
                      f"({(time.perf_counter() - t0) / step:.2f}s/step)")
        sched.step()
        ev = evaluate(model, tok, val_items, LAYA_GATE_QUESTIONS)
        print(f"[train] epoch {epoch} done ({time.perf_counter() - t0:.0f}s) train_loss={tot_loss / tot_n:.4f} "
              f"val_loss={ev['loss']:.4f} val_acc={ev['acc']:.4f}  per-q={ {k: round(v, 3) for k, v in ev['per_q'].items()} }")

    # 保存可加载 checkpoint
    args.out.mkdir(parents=True, exist_ok=True)
    from safetensors.torch import save_file
    sd = {k: v.contiguous() for k, v in model.state_dict().items()}
    save_file(sd, args.out / "model.safetensors")
    shutil.copy2(cache / "rl_agent_config.json", args.out / "rl_agent_config.json")
    if (args.out / "tokenizer").exists():
        shutil.rmtree(args.out / "tokenizer")
    shutil.copytree(cache / "tokenizer", args.out / "tokenizer")
    if (args.out / "encoder").exists():
        shutil.rmtree(args.out / "encoder")
    shutil.copytree(cache / "encoder", args.out / "encoder")
    print(f"[out] checkpoint saved: {args.out}")


if __name__ == "__main__":
    main()
