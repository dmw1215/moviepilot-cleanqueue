# 清理队列与白名单外数据（CleanQueue）

MoviePilot 插件：清理待整理队列，以及清理白名单以外的全部业务数据。

## 功能

### 一、清理整理队列

只清空待整理队列及关联执行步骤（`transferpending` 及其关联的 `transferexecutionstep` 记录），不影响整理历史、订阅、下载记录等其他数据。

### 二、清理白名单以外的数据

清空白名单以外的全部业务数据，不只是整理队列。仅操作数据库，不动配置文件与插件目录（`app.env`、`plugins/`、`local_plugins/`、`plugins_backup/`、`plugin_transactions/`）。

> ⚠ 危险操作：删除后无法恢复，只能从备份文件手动恢复数据库。执行前请确认这些数据确实不需要了。

#### 白名单（保留以下 13 张表）

| 表 | 说明 |
| --- | --- |
| user | 管理员账号、密码、权限 |
| userconfig | 用户个人设置 |
| systemconfig | 系统全局设置 |
| site | PT 站点、Cookie、认证信息 |
| plugindata | 插件配置和数据 |
| pluginidentity | 插件身份信息 |
| plugininstallation | 插件安装记录 |
| plugininstance | 插件实例和配置 |
| passkey | 通行密钥 |
| alembic_version | 数据库版本 |
| siteicon | 站点图标 |
| sitestatistic | 站点统计 |
| siteuserdata | 站点用户统计 |

#### 将清理的表

agentchat（AI 对话历史）、agentinvocation（AI 调用记录）、agenttaskrun（AI 任务执行历史）、agenttask（AI 任务及定时任务配置）、downloadhistory（下载历史）、downloadfiles（下载文件索引）、transferhistory（媒体整理历史）、mediaserveritem（媒体服务器索引）、subscribehistory（订阅历史）、message（消息历史）、workflow（自定义工作流）、subscribe（订阅任务）、transferpending（待整理任务）、downloadfailure（下载失败记录），以及其他所有不在白名单中的表。

注意：自定义工作流（workflow）不在白名单内，会被清空。

## 安全机制

- 两个操作都会先自动备份数据库，失败自动回滚
- 每次操作都在应用运行时直接进行，不停容器
- 操作记录保存在插件数据中，可在详情页查看最近记录

## 安装

1. 将 `cleanqueue` 目录复制到 MoviePilot 插件目录（容器内 `/app/app/plugins/cleanqueue/`，持久副本 `/config/plugins-local/cleanqueue/`）
2. 在数据库 `plugininstance` 表中插入一行：`instance_id='CleanQueue'`、`source_plugin_id='CleanQueue'`、`is_enabled=1`
3. 在 `systemconfig` 的 `UserInstalledPlugins` 清单中追加 `'CleanQueue'`
4. 重启 MoviePilot，日志出现「加载插件：CleanQueue 版本：x.x.x」即安装成功

容器重建（镜像更新）会抹掉容器内代码，从持久副本复制回去并重启即可恢复，数据库里的注册不受影响。

## 版本历史

- **v1.2.0**：详情页按两张卡片分开，按钮下方加注释；第二个功能的警告加强（红色警示、确认弹窗、按钮注释均注明删除后无法恢复，只能从备份手动恢复）
- **v1.1.0**：「精简配置」改名「清理白名单以外的数据」；详情页加确认开关（清理队列一个，清理白名单以外的数据两个：确认 + VACUUM，默认开）；页面写出白名单与将清理的表
- **v1.0.0**：首版，清理整理队列与精简配置，操作前自动备份
