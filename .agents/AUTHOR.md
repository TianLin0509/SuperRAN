# 工作位合同 —— SuperRAN

**按本文件工作。** 这次要做的，是维护者在群聊里布置的那一件事；他没说清就先问一句，不要猜。

> **开工前先读两份很短的，不读会白干：**
> - `.agents/OUTPUT.md` —— 怎么跟维护者说话。他是**无线通信工程师，不是软件工程师**，
>   他原话是「你说的东西我看得很费劲，理解不了」。**他看不懂你在干什么，你干得再对也等于没干。**
> - `.agents/TESTING.md` —— 这个仓库有**两个会让你得出假结论的坑**。
>   不读就报「测试通过」，你的结论多半是错的。

---

## 一、先开自己的 worktree

主工作区 `C:\Vibe\Wireless\SuperRAN` **不是干活的地方**，`.githooks/pre-commit` 会拒绝在那里提交。

```bash
git worktree add C:/Vibe/Worktrees/SuperRAN/<日期>-<任务简称>-<你的席位> -b <分支名> develop
```

- 目录名带**席位**（`claude1` / `codex1`），并行的几个人才不会互相踩
- 分支名用 `feat/` `fix/` `chore/` 开头 + 一句能看懂在干嘛的短语 + 日期

### 开跑前必做这一步，不做等于白测

```bash
export PYTHONPATH='C:\Vibe\Worktrees\SuperRAN\<你的目录>\src'
python -c "import superran; print(superran.__file__)"
```

打印出来的路径**必须**在你自己的工作区里。不是就停下来修，别继续。

原因见 `TESTING.md` 坑 1：editable 安装把 `import superran` **硬指向主仓库**。
不设这个变量，你测的是主仓库的旧代码，而读文件的断言用的又是你工作区的文件——
**同一次测试里两个真相，且不报错。**

---

## 二、三条铁律（SuperRAN 特有；违反了合并位会直接判 FAIL）

1. **一个提交只动一个物理机制。** 混着改，出问题没法二分定位，审核也没法给结论。
2. **修的是物理 bug，就必须补一条「revert 掉会变红」的测试**，并进
   `tests/test_physics_invariants.py`。这叫棘轮：修过的坑不许再掉进去。
   合并位会**亲自反证**——把你的新测试拿去跑未修复的主干，它必须红。不红就是这条测试没抓住 bug。
3. **不许静默降级。** 用了工程近似、简化模型、缩小场景，都要写进报告的「没证明什么」。
   悄悄降精度换绿灯，是这个仓库最严重的问题。

改动的风险档位查 `.agents/RISK.md` 的文件路径表，**不许自己估**。

---

## 三、自测

聚合入口是 `scripts/run_test_matrix.py`。它按**直接入口**跑每个测试文件、逐文件超时、
全绿才返回 0。**不要用 `pytest tests/` 当判据**——它只收集到 29 个文件里的 18 个，
另外 11 个是脚本式测试，在 pytest 下收集到 0 个用例（见 `TESTING.md` 坑 2）。

```bash
python scripts/run_test_matrix.py --only test_scheduler_edf.py --only test_system.py   # 相关的，几秒到 2 分钟
python scripts/run_test_matrix.py --tier quick    # 快档 18 个文件，约 4.5 分钟
python scripts/run_test_matrix.py --tier full     # 全量 29 个文件，约 7 分钟
```

（2026-09-05 在主目录实测：全量 29/29 通过，421 秒。最慢的是 `test_system.py` 132 秒。）

**合并闸门跑的是 `--tier full`。** 你自测跑快档没问题，但心里要清楚：
合并位那一步会把全量跑一遍，物理档那 11 个文件你没跑过的，到那时会暴露。

- 按 `CLAUDE.md` 里「改哪个文件跑哪些测试」那张表选，不要无脑跑全量
- `RISK.md` 判红档的改动，加跑相邻物理模块
- 报告里写清**跑了哪些、没跑哪些**，不要用「全量通过」这种话
- 失败就如实报。**不许**为了变绿去放宽断言；数值锚点因基线变化而失效时，
  去调场景参数让它重新成立，或者停下来说明

---

## 四、交给合并位

```bash
git push -u origin <你的分支>
```

推 `develop` / `main` 会被 `pre-push` 挡住——**这是对的**，主干只有一个入口（合并脚本）。
你的活是把分支推上去，不是自己合。

推完在群里说一句：**「分支 `<名字>` 可以审了」**，然后停下等合并位。
**不要**自己去建 PR、不要动主干、不要替合并位跑合并。

---

## 五、最后交这四行人话（标签固定，一个字都别改）

不要贴代码，不要贴日志，不要写函数名行号。

```
PROGRESS: 一句话说清你干了什么，讲无线不讲代码
VERIFIED: 实际跑了什么、什么结果，写数字（例：run_test_matrix quick 档 18/18 通过）
RISK: 有什么要注意的；没有就写「无」
REPORT: HTML 报告的绝对路径；没有就写「无」
```

需要出 HTML 报告时用 `python scripts/make_agent_report.py <report.json>`，
字段照抄 `.agents/report.example.json`，**不要手写 HTML**。

---

## 六、收到 BLOCKERS 之后

合并位判 FAIL 会给你一份 `BLOCKERS`。**只修 BLOCKERS 里列的东西**，
别顺手改别的——那会让下一轮审查失去对照，也会让你自己说不清是哪一处修好的。

改完推**同一个分支**，再在群里说一句可以审了。
