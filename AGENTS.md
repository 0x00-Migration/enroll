# enroll

开发者在本仓库开 issue 领取仓库, 一个应用一个: 机器人从模板仓库 `app-template` 生成 `app-<应用名>-<GitHub 用户名>` (公开), 把它绑定到申请人的数字 GitHub ID, 保护默认分支, 再邀请申请人以 write 权限加入. 每个仓库只有领取人能推, 互相推不到对方的仓库. 作品人工审核, 这里没有自动评分.

领取链接: `https://github.com/<组织>/enroll/issues/new/choose`.

## 文件

- `.github/ISSUE_TEMPLATE/enroll.yml`: 领取表单, 标题固定为 `领取仓库` (标题不是它的 issue 机器人不理); 必填的「应用名」由 `enroll.py` 的 `app_name` 从 issue 正文的 `### 应用名` 一节读出, 转小写,
  只许小写字母、数字和单个 `-` 连接, 最多 40 个字符. 改表单里这一项的 label 要同时改 `APP_LABEL`.
- `.github/workflows/enroll.yml`: issue 打开时运行 `enroll.py`; 手动运行可填 issue 号重试.
- `enroll.py`: 领取逻辑, 可重复运行 (中途失败重跑会接着做, 不会重复建仓库或重复邀请).
- `github.py`: 只用标准库的 REST 客户端 (重试、限流、刚创建的资源暂时 404).
- `tests/`: `python3 -m unittest discover -s tests`, 用内存里的假 GitHub.

## 规则

- 仓库归属以仓库变量 `STUDENT_ID` (数字 ID) 为准, 用户名会变; `STUDENT_LOGIN` 每次领取时更新, `APP_NAME` 记应用名. 开发者只有 write 权限, 改不了仓库变量.
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
- 模板里的 acdev 版本在 `app-template/.github/workflows/check.yml` 的 `ACDEV_VERSION` 和 `package.json` 的 `@acde/api` 两处, 一起改. 已生成的仓库不会跟着变.
