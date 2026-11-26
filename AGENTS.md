# enroll

开发者在本仓库开 issue 领取仓库, 一个应用一个: 机器人从模板仓库 `app-template` 生成 `app-<应用名>-<GitHub 用户名>` (公开), 把它绑定到申请人的数字 GitHub ID, 保护默认分支, 再邀请申请人和队友以 write 权限加入. 每个仓库只有本队的人能推, 推不到别的队的仓库. 作品人工审核, 这里没有自动评分.

领取链接: `https://github.com/<组织>/enroll/issues/new/choose`.

## 文件

- `.github/ISSUE_TEMPLATE/enroll.yml`: 领取表单, 标题固定为 `领取仓库` (标题不是它的 issue 机器人不理); 必填的「应用名」由 `enroll.py` 的 `app_name` 从 issue 正文的 `### 应用名` 一节读出, 转小写,
  只许小写字母、数字和单个 `-` 连接, 最多 40 个字符. 选填的「队友」由 `teammates` 从 `### 队友` 一节读出: 最多 3 个 GitHub 用户名, 用空格、逗号或换行隔开, 可以带 `@`.
  改表单里这两项的 label 要同时改 `APP_LABEL`、`TEAM_LABEL`.
- `.github/workflows/enroll.yml`: issue 打开时运行 `enroll.py`; 手动运行可填 issue 号重试.
- `enroll.py`: 领取逻辑, 可重复运行 (中途失败重跑会接着做, 不会重复建仓库或重复邀请).
- `github.py`: 只用标准库的 REST 客户端 (重试、限流、刚创建的资源暂时 404).
- `tests/`: `python3 -m unittest discover -s tests`, 用内存里的假 GitHub.

## 队伍

- 一队 1 到 4 人, 一个人只能在一个队. 开 issue 的人是队长, 只有队长能领仓库, 仓库名里是队长的用户名.
- 队长在「队友」里写其他队员. 以后要加人, 队长用同一个应用名再开一个 issue, 写上新队员 (已经在队里的人写不写都行).
- 一个队可以领多个应用. 队里每个人都会被邀请进本队所有的仓库: 后加的队员也能推之前的仓库, 新领的仓库会邀请全队.
- 拒绝 (不建、不改任何东西): 申请人已经是别的队的队员; 写的队友已经在别的队 (当队长或队员); 加上后超过 4 人.
- 机器人只加人, 不删人. 删人和换队由维护者做, 见「运维」.

## 规则

- 仓库归属以仓库变量 `STUDENT_ID` (队长的数字 ID) 为准, 用户名会变; `STUDENT_LOGIN` 每次领取时更新, `APP_NAME` 记应用名.
  `MEMBER_IDS` 和 `MEMBER_LOGINS` 记全队的数字 ID 和用户名, 逗号分隔, 队长在前, 其余按加入的先后. 开发者只有 write 权限, 改不了仓库变量和仓库描述.
- 仓库描述是索引, 不要手改: 机器人把它写成 `@队长 @队员 ...`. 每次领取时机器人读组织的仓库列表 (一次请求 100 个, 带描述), 靠描述找出每个仓库属于哪个队, 不逐个读变量.
- 队长的仓库: 名字以 `PREFIX` 开头, 没归档, 不是模板, 描述里第一个是队长, 并且 `STUDENT_ID` 是队长的 ID. 全队名单是这些仓库 `MEMBER_IDS` 的并集 (没有 `MEMBER_IDS` 的旧仓库只算队长一人), 再加上这次写的队友. 归档的仓库哪里都不算.
- 每次领取, 机器人按数字 ID 查出全队现在的用户名, 写进这次领取的仓库的描述、`MEMBER_IDS` 和 `MEMBER_LOGINS` (先写描述, 中途失败也不会让别的队抢走新队员), 再确认全队每个人在本队每个仓库都有 write 权限或有效的邀请.
- 用户名改了: 描述里还是旧用户名, 要等队长下次领取才更新; 在那之前别的队能把改了名的队员加进去. 队长改了用户名, 机器人认不出他原来的仓库. 这两种都请维护者处理.
- 应用名和用户名都能含 `-`, 名字可能撞上 (`foo` + `bar-baz` 和 `foo-bar` + `baz`): 后来的人因 `STUDENT_ID` 不符被拒, 换个应用名即可.
- 名字对上但 `STUDENT_ID` 是别人的: 拒绝 (多半是改名后用户名被别人注册). 名字对上但不是从模板生成的, 或没绑定却已有外部协作者 / 邀请: 不动, 报错给维护者.
- 默认分支由仓库级 ruleset `history` 禁止删除和 force push (免费组织没有组织级 ruleset, 所以逐个仓库设).
- issue 的评论和关闭用 workflow 自带的 `GITHUB_TOKEN`; 建仓库、变量、ruleset、邀请用 GitHub App 的令牌.

## 首次配置 (要组织 owner)

1. 在组织下建 GitHub App, 不要 webhook. Repository permissions: Administration 读写, Variables 读写, Contents 只读, Metadata 只读. 装到组织, 选 All repositories.
   如果从模板生成仓库时报和 workflow 有关的权限错误, 再加 Workflows 读写.
2. 本仓库 Settings → Secrets and variables → Actions: 变量 `ENROLL_APP_CLIENT_ID` (App 的 Client ID), secret `ENROLL_APP_KEY` (App 的私钥).
   没有 App 时可改用 owner 的 fine-grained PAT, 放进 secret `ENROLL_TOKEN`.
3. `app-template` 在 Settings 里勾上 Template repository, 保持公开.
4. 组织 Settings → Member privileges → Base permissions 设成 Read 或 No permission, 否则组织成员能推所有开发者的仓库.
5. 可选变量 `TEMPLATE`、`PREFIX` 覆盖默认的 `app-template` 和 `app-` (仓库名是 `<PREFIX><应用名>-<用户名>`).

## 运维

- 失败: 机器人在 issue 里留言并保持打开, 看 Actions 日志, 修好后手动运行 `Enroll` 并填 issue 号.
- 停止领取: 在 Actions 里禁用 `Enroll` workflow.
- 删人或换队: 在这个队每个仓库的描述、`MEMBER_IDS` 和 `MEMBER_LOGINS` 里删掉他, 再在这些仓库的 Settings → Collaborators 撤掉他的权限或邀请. 漏掉一个仓库, 下次队长领取时他会被加回本队所有仓库.
- 模板里的 acdev 版本在 `app-template/.github/workflows/check.yml` 的 `ACDEV_VERSION` 和 `package.json` 的 `@acde/api` 两处, 一起改. 已生成的仓库不会跟着变.
