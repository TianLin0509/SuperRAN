"""Generate the team-facing kickoff document and portable onboarding pack."""
import html
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
ARCHIVE_NOTICE = ('> 历史开工资料（2026-09-25），仅供追溯，不再作为当前启动要求。\n'
                  '> 当前开发以仓库 AGENTS.md 与 .agents/SYNC.md 为准；下文分工和旧交接指令不构成现行授权。\n\n')
PEOPLE = [
 dict(id='lin',name='林田',area='基础版本与共同规则',task='把团队研究接到一个可维护的底座上',
 question='哪些能力可以直接研究，哪些必须先补校验或扩展？',
 first='确认首轮代码基线、代码交接通道和合并执行者；与一位同事跑通安装、上报和一次独立复核。',
 case='选一个已有小场景，保留配置、结果与代码版本；作为所有人安装后的共同起点，不把重复跑通当作物理正确性证明。',
 day10='交付首轮能力边界与接入说明；完成至少一次跨电脑代码交接演练，明确每项任务的复核搭档。',
 later='处理跨模块接口与物理假设分歧，按实际验收结果决定进入主干的能力；不承诺新算法必有收益。',
 asset='共同基线、复核入口、跨电脑交接规则。',review='王迅泽协助组织集成演练；实现与审核必须分会话。',
 limit='不把所有人的实现和首轮检查集中到林田；只有跨域争议、重要假设和发布范围回到队长。',
 confirm='首轮统一代码的发放方式、每周可投入的复核时间。'),
 dict(id='ren',name='任荣',area='阵列与波束',task='U6G DHBF 选波束：先建立可信参考',
 question='在明确的阵列、射频连接和功率约束下，候选选波束策略表现如何？',
 first='把 DHBF 的阵列形态、连接约束、候选码本和优化目标画成一页；由 agent 核对当前实现缺口。',
 case='先限定一个窄带、单用户、候选集合足够小的场景，在同一约束下穷举所有候选，得到可核验的最优值。',
 day10='交出可重跑的穷举参考、一个候选方法及差异解释；若底座缺能力，交出最小失败例和单项扩展方案。',
 later='必要物理扩展与研究算法分开交付；小场景验证后再扩到约定的带宽、用户数与信道条件。',
 asset='阵列/波束约束、有限候选参考案例、相关回归用例。',review='建议张敏勤复核参考条件，王迅泽复核系统接口；具体角色会上确认。',
 limit='DHBF 的具体含义和硬件约束由任荣定义；有限候选最优不代表任意算法的全局最优。旧问题先按当前版本复核。',
 confirm='首轮硬件约束、码本大小和目标指标。'),
 dict(id='xie',name='谢涛羽',area='链路自适应',task='AI AMC / Network Charting：先跑通传统基线',
 question='相同可用反馈、信道和开销下，新方法相对传统自适应调制编码方法有什么变化？',
 first='与林田明确 Network Charting 的具体输入、输出、反馈时延及研究主张；先画出传统闭环。',
 case='核对调制编码表与传输块口径，选择一个固定、可实现目标误块率的场景，检查反馈时序和外环调整方向。',
 day10='交出传统基线的可复现实验、关键口径核对与一条异常诊断；有未对齐之处时先报告缺口，不急于接 AI。',
 later='传统链路校验后，按预先约定的基线和主指标接入候选方法，报告收益或证据不足。',
 asset='自适应调制编码（AMC）校验、反馈时序用例和方法比较案例。',review='建议任荣协助物理复核，张敏勤协助反馈/测量接口复核。',
 limit='外环收敛依赖可达目标、反馈和步长等条件；不能把“必到10%”当通用判据，也不预先承诺 AI 优于基线。',
 confirm='Network Charting 的准确范围，以及首轮基线和反馈预算。'),
 dict(id='zhangm',name='张敏勤',area='SRS 与信道测量',task='SRS 开销与估计质量：建立受控对照',
 question='探测参考信号（SRS）的资源开销与估计质量，怎样影响约定场景下的链路表现？',
 first='固定移动性、估计器、资源配置和指标；列出名义周期到实际生效周期的核对方法。',
 case='先做静态、无噪声或其他有明确参考的简化校验，再只改变一个 SRS 条件；记录实际资源与时间轴。',
 day10='交出一个参考校验和一组受控配置的可运行案例，解释开销、估计误差及当前支持的链路边界。',
 later='扩大到约定的移动性与干扰条件，确认哪些结果可用于研究、哪些仅为工程近似。',
 asset='SRS 资源和估计相关回归用例、配置回显核对与边界说明。',review='建议任荣复核物理假设，王迅泽复核系统衔接。',
 limit='不预设周期越长、吞吐就一定越低；开销与估计老化可能同时变化。系统链路是否接通须先实测。',
 confirm='本轮选择周期、端口还是复用关系作为唯一变量。'),
 dict(id='wang',name='王迅泽',area='系统级与集成',task='一个调度问题贯穿业务、资源与体验',
 question='在固定业务与资源条件下，候选调度策略怎样影响约定的吞吐或完成时延指标？',
 first='选择一个有限到达业务的小场景，画出“到达→排队→调度→重传→交付”的证据链，固定主指标。',
 case='先核对到达、已发和积压的字节守恒，以及资源不能重复分配；再建立传统调度基线。',
 day10='交出一条可追溯的系统级案例、一份指标口径说明，并协助完成首个跨电脑交接演练。',
 later='只在已验基线上比较候选方法；安排每周两次短合并窗口，组织独立会话执行，不包揽所有审核。',
 asset='系统级回归案例、指标口径、集成交接实例。',review='建议任荣复核物理链路，张敏勤复核反馈与跨模块一致性。',
 limit='当前是建议研究题，会上定稿；满缓冲与有限业务不能混用指标，历史收益不能跨版本直接引用。',
 confirm='选吞吐还是完成时延作首轮主指标；集成协调的时间上限。'),
 dict(id='zhangy',name='张燕',area='自然语言实验',task='用对话完成三个有参考依据的实验',
 question='不手动编写仿真代码，能否让 agent 正确理解问题、执行实验，并交出可解释结果？',
 first='与张敏勤挑选3道条件清楚的小题；每题先确认问题、参考来源、适用条件与允许误差。',
 case='从已有能力中选题：固定信道下的测量一致性、无噪声简化场景、一个已有受控对照。题目与参考答案先独立复核。',
 day10='完成3份对话实验记录，逐题写清是否跑通、是否对齐参考、人工介入位置和一个改进建议；失败题同样有价值。',
 later='保持这3题作为固定回归集，再按真实需求增加题目；修正提示和工具说明时允许指挥 agent 修改代码。',
 asset='带参考条件的对话评测题、失败复现和新人操作说明。',review='建议张敏勤复核答案适用条件，陈致霖在约定时段抽看解释。',
 limit='“只通过对话”约束人的实验操作，不禁止 agent 写脚本；不把高跑通率等同于物理正确，也不按问题数量考核个人。',
 confirm='本人愿意开展的3个知识点，以及一次固定答疑时段。'),
 dict(id='song',name='宋飞宇',area='独立参考与复现',task='复现一个条件可还原的公开案例',
 question='平台能否复现一个来自公开标准或论文、输入与评价口径明确的参考结果？',
 first='提出2个候选案例，由任荣共同选1个；把参数缺失、图像读数误差和需要的能力列清楚。',
 case='优先选择可计算的简单参考或公开可重跑的小案例；独立参考不能直接复用被测实现的核心计算。',
 day10='交出来源说明、场景配置、复跑命令和差异表；原文信息不足时明确缺项，不为对上曲线反复调参。',
 later='补成可复跑案例和回归检查，再增加第二个案例；与张燕共享题材时分别检验数值参考和对话过程。',
 asset='独立参考、复现材料、安装或文档中暴露的可重现问题。',review='建议任荣复核参考假设，张燕交叉验证说明能否被另一人使用。',
 limit='论文图并非无条件真值；必须先对齐假设、单位和统计口径。首轮只承诺1个案例。',
 confirm='所选案例与必要依赖是否能在两周内获得。'),
 dict(id='chen',name='陈致霖',area='专家对话研究',task='以一个真实无线问题验证专家研究方式',
 question='专家只通过对话，能否完成从提出问题到审阅证据的研究过程？',
 first='选一个当前模型能够支撑的具体问题；建议从测量误差或干扰影响中选一个，并限定结论范围。',
 case='先与领域负责人确认一个简化参考和反例，再让 agent 设计研究；对模型不支持的环节明确停下说明。',
 day10='交出一份问题与实验约定、一次小试点审阅，以及一条平台必须补充的判断规则；按实际可投入时间推进。',
 later='完成一份可辩护的研究报告或证据不足结论；把研究复盘与专家评议合在一次讨论中。',
 asset='专家判断案例、约束建议和对话研究示范。',review='按所选问题由任荣或张敏勤做领域复核；实际搭档在会上确认。',
 limit='专家研究不放在其他成员首轮交付的关键路径上；时间未确认前不承诺固定工时。',
 confirm='研究题目、参与意愿和可提供的时间。'),
]

COMMON = '''# SuperRAN 开工与日常协作

这是团队开工方案，分工与搭档待开工会确认。网页不是代码仓库，也不是自动派工器。

## 每人第一天
1. 领取林田指定的同一份代码基线和自己的任务书；记录完整提交 SHA（代码版本指纹）。不要把公开远端的最新版本自动当团队基线。
2. 让 agent 完整读取 CLAUDE.md、AGENTS.md 和 INSTALL_AGENT.md，确认依赖、能力、导入路径及一个最小案例。
3. 领取自己的私有站点接入 JSON，在个人电脑设置 SUPERRAN_REPORT_CONFIG。网站打开无需登录；后台上报配置不用交给浏览器或他人。
4. 将 AGENT-INSTRUCTIONS.md 放进该 agent 实际读取的项目指令；公司 GLM 不读 AGENTS.md 时用其项目指令入口。
5. 先检查任务是否已有 work_id。有就 attach；没有才 start，并将返回编号写入本任务笔记。每项任务持续更新同一记录。
6. 与搭档验证：另一台电脑能看到进展；人工修改后 agent 不覆盖；断网补传不重复。没有完成这一步，不能宣称该同事已接入。

## 给 agent 的上报命令（Windows 示例）
下面的配置路径与工作编号为占位符，由 agent 替换。命令在解压后的开工包目录执行；不需要先把网站分支合进仿真主干。

```powershell
$env:SUPERRAN_REPORT_CONFIG = '<维护者私下交付的本人配置JSON路径>'
python report.py --config $env:SUPERRAN_REPORT_CONFIG start --title '<本人任务标题>' --progress '开始核对任务与参考条件。' --source codex
# 保存返回的 work_id；以后不要重复 start：
python report.py --config $env:SUPERRAN_REPORT_CONFIG update --work-id '<原工作编号>' --progress '<已完成、已验证、阻塞、下一步>' --status '进行中'
python report.py --config $env:SUPERRAN_REPORT_CONFIG flush
# 在另一台电脑接续同一任务：
python report.py --config $env:SUPERRAN_REPORT_CONFIG attach --work-id '<原工作编号>'
```

公司 agent 使用 --source company-agent。多行摘要用 --progress-file 指向 UTF-8 文件。
代码主干目前未包含已上线网站分支的接入入口；本包提供独立 report.py 与 AGENT-INSTRUCTIONS.md，按项目指令接入即可。

## 每天怎样工作
人：解释要回答的无线问题，确认条件，判断物理解释，协调阻塞。
Codex：在隔离工作区实现、运行自测，保存配置、证据与提交版本，调用 report.py 上报摘要。
公司 GLM：在可访问的环境阅读 Airview/标准/内部参考，按当前 SYNC.md 返回问题清单、物理论据与最强反例。当前回路不直接接收公司侧 patch。
不同模型不等于独立审查：审核必须使用独立会话，自己读改动、自己运行检查。

## 先校验，再研究，再沉淀
校验：先写来源、条件、期望与容差；不用同一份实现给自己算参考答案。
研究：明确基线、改变/固定条件、主指标和预算；没有支持条件就不下性能结论。
沉淀：必要扩展、可重跑案例、验证证据和边界说明。负结果或证据不足也可以完成研究。
遇到平台缺口：保留失败例与所需能力，拆成一个小改动；不得悄悄换模型获得结果。

## 代码怎样交回来（首轮建议：集中合并）
每人电脑有自己的仓库和 worktree（同一仓库中的隔离工作目录），各自提交；跨电脑不能直接互读本地分支。
首轮用维护者认可的文件交接通道递交 git bundle（保留提交的代码包）和交付说明，不增加远端平台前置条件。
中央维护仓库由独立合并会话导入候选分支，核对完整 SHA，再执行既有 MERGER.md 闸门。
同一仓库内直接交本地分支和 SHA，不需要 bundle。不要直接复制文件覆盖主干。
首轮集中合并与跨电脑代码通道属于方案，需会前用一个小文档改动实测；本开工包没有自动打通网络或修改仓库合同。
后续若采用私有远端，由维护者明确授权后按 SYNC.md 单独接入；日常不自动 push、不创建 PR、不推进 main。

## 独立审核和完成
按 RISK.md 查档：红档必须有 Physics 与 Integration 两个独立审阅角色；黄档一个；绿档按相关检查。
Author 不能审核或合并自己的实现。原开发规则由独立 Merger 执行；人员分工不能取消这些规则。
物理 bug 修复须有修复后通过、未修复基线失败的回归证据。
合并闸门绑定任务和主干完整 SHA；主干变化后重验。主干 develop，main 仅由维护者显式发布。
研究结束、代码合并、版本发布分别说清楚；网站“已完成”只表示本条工作按约定结题，不自动表示已合并。

## 网站只写四句话
已完成：做了什么。
已验证：用什么案例/测试验证，证据在哪里；没有验证就明说。
当前阻塞：需要谁确认哪件事；没有就写无。
下一步：下一个可验收动作。

标题=本项研究；负责人=任务归属；状态=进行中/受阻/已完成；成果链接=本任务持续更新的报告。
任务书先放在该报告/交付材料中，无需等待网站新增“任务说明”字段。
有实质进展、阻塞、交付时上报，不要求每个工具调用都更新。网页人工改过的字段需明确交回后才能由 agent 再写。
脚本返回 ok:true 才算同步成功。离线内容在 outbox（本机待传队列）中；watch 只补传，不会自己产生摘要。
当前尚无强制同步闸门，先在交付说明中检查站点回执。不能把文档约定说成不可绕过的自动机制。

## 每周节奏（建议）
一次20分钟同步，只谈阻塞、接口冲突和需要的人；每人可提前在网站写好。
两次30分钟集中复核窗口，由王迅泽协调不同的独立审核会话，按到齐的交付材料安排。
林田优先处理跨模块假设、资源取舍和未解决争议，不逐项代做。
双周用结果演示替代口头百分比。优先看可复现性与证据，不比较代码行数、问题数量或正向收益数量。

## 公开网页范围
记录公开可展示的任务与进度摘要。个人评价不写入团队页；内部原始数据、专有代码与未获授权材料留在对应工作环境。
Airview 是参考之一，差异要解释，不能默认另一平台必然正确。
'''

HANDOFF = '''# 一项工作的交付说明

任务标题 / 负责人：
站点 work_id / 成果链接：
任务目标和本次实际范围：
基线完整 SHA / 任务完整 SHA：
本地分支 / 跨电脑 bundle 文件（如需）：
改动文件与风险档：
参考答案来源、条件、容差：
已运行的检查、结果及复跑方式：
物理 bug 的修复后绿 / 未修复红证据（如适用）：
尚未证明什么、已知限制：
独立复核角色及结论（未审就写待审）：
站点上报回执（未同步就写未同步）：
结题范围：研究结束 / 已合并 / 已发布（分别说明）：

每项材料能链接则链接，不重复写长报告。没有代码改动时，提交与合并项写“不涉及”。
'''

def prompt(p):
    return f'''【历史任务摘录，仅供追溯】当前开工请先读取仓库 AGENTS.md 与 .agents/SYNC.md；下面的旧基线、bundle 和远端指令不作为现行流程或授权。
我是{p['name']}，参与 SuperRAN 团队工作。请先读取 CLAUDE.md、AGENTS.md、INSTALL_AGENT.md 和本任务书；当前代码基线由林田发放，不自行切换到远端最新版本。
本任务：{p['task']}。
要回答：{p['question']}
前两个有效工作日：{p['first']}
首个校验：{p['case']}
第10个有效工作日交付：{p['day10']}
重要边界：{p['limit']}
先复述研究问题与当前能力，最多澄清三个会改变结果的条件，再做最小案例。有平台缺口时报告，不悄悄降级。
需要改代码时按 AUTHOR.md 在独立 worktree 实现、自测、本地提交；不得自己审核或合并，不自动 push 或建 PR。公司 GLM 审阅按 SYNC.md 返回问题清单。
站点：https://ai.lt-stockpartner.tech/superran/ 。配置已有时读取 AGENT-INSTRUCTIONS.md，已有 work_id 就接续；没有时创建一次并保存编号。有进展、阻塞或交付时主动上报；没有凭证先报告接入缺口，不借用别人的凭证。
请先给我：当前能力与缺口、首个可验收动作、参考依据和需要我确认的条件。所有进展必须区分已实现、已验证、已合并。'''


def main():
    people = OUT/'tasks'
    people.mkdir(exist_ok=True)
    for p in PEOPLE:
        text=f"# {p['name']}｜{p['task']}\n\n" + ARCHIVE_NOTICE + "历史状态：开工会讨论稿，待本人确认。\n\n"
        for key,label in [('question','研究问题'),('first','前两个有效工作日'),('case','首个校验'),('day10','第10个有效工作日交付'),('later','后续六周'),('asset','平台沉淀'),('review','复核协作建议'),('limit','边界'),('confirm','会上确认')]:
            text+=f'## {label}\n\n{p[key]}\n\n'
        text+='## 交给 agent 的开工指令\n\n'+prompt(p)+'\n'
        (people/(p['id']+'.md')).write_text(text,encoding='utf-8')
    (OUT/'TEAM-GUIDE.md').write_text(COMMON.replace('\n\n', '\n\n' + ARCHIVE_NOTICE, 1),encoding='utf-8')
    (OUT/'HANDOFF.md').write_text(HANDOFF.replace('\n\n', '\n\n' + ARCHIVE_NOTICE, 1),encoding='utf-8')
    template=(OUT/'template.html').read_text(encoding='utf-8')
    data=json.dumps([{**p,'prompt':prompt(p)} for p in PEOPLE],ensure_ascii=False).replace('</','<\\/')
    (OUT/'index.html').write_text(template.replace('@@PEOPLE@@',data),encoding='utf-8')
    package=ROOT/'artifacts/kickoff-20260925'
    package.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(package/'superran-kickoff-pack.zip','w',zipfile.ZIP_DEFLATED) as z:
        for name in ['index.html','TEAM-GUIDE.md','HANDOFF.md']:
            z.write(OUT/name,name)
        for f in people.glob('*.md'):
            z.write(f,'tasks/'+f.name)
        z.write(ROOT/'apps/team_site/report.py','report.py')
        z.write(ROOT/'.agents/TEAM_SITE.md','AGENT-INSTRUCTIONS.md')
        z.writestr('agent-config.example.json',json.dumps({'site':'https://ai.lt-stockpartner.tech/superran','member':'由维护者分配的成员编号','agent_token':'由维护者单独交付，不在会议页或共享包中分发'},ensure_ascii=False,indent=2))
    shutil.copyfile(package/'superran-kickoff-pack.zip',OUT/'superran-kickoff-pack.zip')
    print(str(OUT/'index.html'))
    print(str(package/'superran-kickoff-pack.zip'))


if __name__=='__main__':
    main()
