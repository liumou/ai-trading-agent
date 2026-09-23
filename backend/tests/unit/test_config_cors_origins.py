"""
CORS 白名单解析（`Settings.cors_origin_list`）单元测试。

背景：`backend/.env` 里 `CORS_ORIGINS` 的一个拼写错误
（`http:/localhost:3000`，少一个斜杠）会让这个 origin 静默失效 —— 浏览器端表现成
每个预检请求都收到 `400 Disallowed CORS origin`，而服务端只有一行
"Disallowed CORS origin" 访问日志，排查成本极高。

覆盖：
- 非法条目（缺斜杠 / 非 http(s) scheme / 带路径或 query / 空白）被丢弃
- 合法条目保留：含端口、尾斜杠归一、去重、顺序保持
- `*` 原样保留（`auth._assert_auth_consistent()` 依赖它拒绝启动）

注意：用 `patch.object(settings, ...)` 而非新建 `Settings()` —— 后者会按 CWD 加载
backend/.env，测试结果会随本机配置漂移（与 test_provider.py 的既有风格一致）。
"""

from unittest.mock import patch

from app.config import Settings, settings


class TestCorsOriginListNormalization:
    @staticmethod
    def _parse(raw: str) -> list[str]:
        with patch.object(settings, "cors_origins", raw):
            return settings.cors_origin_list

    def test_typo_missing_slash_is_dropped(self):
        # 回归：这条错误配置曾让前端所有带 Authorization 的预检请求收到 400
        assert self._parse("http:/localhost:3000") == []

    def test_valid_origins_survive_alongside_bad_entry(self):
        raw = "http:/localhost:3000,http://192.168.3.34:3000,https://app.example.com"
        assert self._parse(raw) == ["http://192.168.3.34:3000", "https://app.example.com"]

    def test_whitespace_and_trailing_slash_normalized_and_deduped(self):
        raw = " http://localhost:3000/ , http://localhost:3000 ,http://localhost:8002 "
        assert self._parse(raw) == ["http://localhost:3000", "http://localhost:8002"]

    def test_order_preserved(self):
        raw = "https://b.example.com,http://a.example.com"
        assert self._parse(raw) == ["https://b.example.com", "http://a.example.com"]

    def test_empty_entries_ignored(self):
        assert self._parse(",,   ,") == []

    def test_non_http_scheme_dropped(self):
        assert self._parse("ws://localhost:8002,ftp://host:21") == []

    def test_path_or_query_dropped(self):
        assert self._parse("http://localhost:8002/api,http://localhost:8002?x=1") == []

    def test_wildcard_survives_for_startup_guard(self):
        # auth._assert_auth_consistent() 靠 "*" 出现在白名单里来拒绝启动，不能被过滤吞掉
        assert self._parse("*,http://localhost:3000") == ["*", "http://localhost:3000"]

    def test_field_default_is_itself_valid(self):
        # 字段默认值必须能通过自己的校验，否则默认配置下 localhost 会静默失效
        default = Settings.model_fields["cors_origins"].default
        assert self._parse(default) == ["http://localhost:3000"]
