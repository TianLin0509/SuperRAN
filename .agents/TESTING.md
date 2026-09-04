# 怎么跑测试（Author 与 Reviewer 都必读，很短）

这个仓库有**两个坑会让你得出假结论**。不读这页就报「测试通过」，结论很可能是错的。

## 坑 1：你跑的可能不是你自己的代码

`site-packages` 里的 `__editable__.superran-0.1.0.pth` **硬指向主仓库**
`C:\Vibe\Wireless\SuperRAN\src`。所以在任何 worktree 里 `import superran`
拿到的都是主仓库的代码，**不是你这个工作区的代码**。

而读文件的断言（`CLAUDE.md`、`skills/`、`docs/`）用的又是你工作区的文件——
同一次测试里两个真相，且不报错。

**开跑前先做这一步，不做等于白测：**

PowerShell：
```powershell
$env:PYTHONPATH = "C:\Vibe\Worktrees\SuperRAN\<你的目录>\src"
python -c "import superran; print(superran.__file__)"
```

Git Bash：
```bash
export PYTHONPATH='C:\Vibe\Worktrees\SuperRAN\<你的目录>\src'
python -c "import superran; print(superran.__file__)"
```

打印出来的路径**必须**在你自己的工作区里。不是就停下来修，别继续。

> Reviewer 尤其注意：你的任务是审某个 SHA。导入错了，你审的就是主仓库当时的代码，
> 整份审核结论作废。

## 坑 2：`pytest tests/` 只覆盖一半的文件

28 个测试文件里，**12 个在 pytest 下收集到 0 个用例**——它们是脚本式测试，
真理在 `if __name__ == "__main__"` 入口。**只跑 pytest 就宣布「全量通过」是不成立的。**

这 12 个必须单独跑，看退出码：

```
test_system              test_physics_invariants  test_gates       test_rng
test_e2e                 test_linklevel           test_mumimo      test_csi_aging
test_results             test_sysscenes           test_raytracing  test_interference
```

PowerShell：
```powershell
$env:PYTHONIOENCODING = "utf-8"
python tests/test_system.py
$LASTEXITCODE          # 0 才算过
```

Git Bash：
```bash
export PYTHONIOENCODING=utf-8
python tests/test_system.py; echo $?
```

**`PYTHONIOENCODING=utf-8` 不能省。** 这些测试打印中文，Windows 控制台默认 cp1252，
输出重定向到文件时会抛 `UnicodeEncodeError` 变成**假失败**（1 秒内退出码 1）。
看到秒退的失败先怀疑编码，不要以为是测试挂了。

想确认当前这一批 pytest 到底覆盖了哪些文件，别凭记忆——跑一下，看输出里出现了哪些文件名：

```
python -m pytest tests/ --collect-only -q
```

## 跑哪些

按 `CLAUDE.md` 里「改哪个文件跑哪些测试」那张表选，不要无脑跑全量。
`.agents/RISK.md` 判定为红档的改动，要加跑相邻物理模块。

## 报告里怎么写

- 写清**跑了哪些**、**没跑哪些**，不要用「全量通过」这种话
- 写清 `superran.__file__` 指向哪里（证明你测的是自己的代码）
- 失败就如实报，**不许**为了变绿去放宽断言。
  数值锚点因为基线变化而失效时，去调场景参数让它重新成立，或者停下来说明
