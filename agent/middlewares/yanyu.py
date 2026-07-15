"""雁羽平台集成中间件。"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import re
import requests
import yaml
from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ModelRequest,
    ModelResponse,
    ResponseT,
)
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

# === Schemas ===

class PublishInfoSchema(BaseModel):
    """`publish_info` 的输入模式。"""
    description: str = Field(
        description="要发布的公告或社交活动的详细描述。这是自然语言文本。"
    )
    extra: dict | None = Field(
        default=None,
        description="可选的结构化键值对参数。推荐指定与场景匹配的 'type'（例如 {'type': 'ball'}）。"
    )
    attachments: list[str] = Field(
        default=[],
        description="要附加的可选本地绝对文件路径列表（例如图片、文档）。"
    )

class ListInfosSchema(BaseModel):
    """`list_infos` 的输入模式。"""
    list_type: str = Field(
        default="published",
        description="要列出的公告类型。必须是 'published'（自己发布的公告）或 'following'（自己关注/加入的公告）。默认为 'published'。",
    )
    status: str | None = Field(
        default=None,
        description="可选的状态过滤条件。必须是：'active'（活跃）、'archived'（归档）、'expired'（过期）、'deleted'（已删除）之一。（仅当 list_type 为 'published' 时适用）。",
    )

class SearchInfosSchema(BaseModel):
    """`search_infos` 的输入模式。"""
    query: str = Field(
        default="",
        description="用于在平台上查找相关公告或活动的搜索查询。可以为空以浏览活跃的活动。",
    )
    status: str | None = Field(
        default="active",
        description="可选的状态过滤条件。必须是：'active'（活跃）、'archived'（归档）、'expired'（过期）、'deleted'（已删除）之一。默认为 'active'。",
    )
    top_k: int = Field(
        default=10,
        description="要返回的最大搜索结果数。",
    )

class FollowUnfollowInfoSchema(BaseModel):
    """`follow_unfollow_info` 的输入模式。"""
    info_id: str = Field(
        description="要关注或取消关注的公告/活动的唯一 ID (UUID)。"
    )
    action: str = Field(
        description="要执行的操作。必须是 'follow'（关注/报名/加入）或 'unfollow'（取消关注/取消报名）之一。"
    )

class ListSkillsSchema(BaseModel):
    """`list_skills` 的输入模式。"""
    only_mine: bool = Field(
        default=False,
        description="如果为 True，则仅列出自己上传的技能包（包括待审核/已拒绝的包）。如果为 False，则列出所有上线的已批准包。",
    )

class UploadSkillSchema(BaseModel):
    """`upload_skill` 的输入模式。"""
    package_name: str = Field(
        description="要上传的技能包的唯一名称。"
    )
    zip_path: str = Field(
        description="技能包 ZIP 文件的绝对本地文件路径。"
    )

class DownloadSkillSchema(BaseModel):
    """`download_skill` 的输入模式。"""
    package_id: str = Field(
        description="要下载的技能包的唯一 ID (UUID)。"
    )


class SearchMarketplaceSchema(BaseModel):
    """`search_marketplace` 的输入模式。"""
    query: str = Field(
        default="",
        description="搜索关键词（在商品标题和描述中搜索）",
    )
    category: str | None = Field(
        default=None,
        description="商品分类筛选：教材|数码|外设|生活|出行|考研|其他",
    )
    min_price: float | None = Field(default=None, description="最低价格")
    max_price: float | None = Field(default=None, description="最高价格")
    sort_by: str = Field(
        default="created_at",
        description="排序字段：price|created_at|view_count",
    )


class PublishMarketplaceSchema(BaseModel):
    """`publish_marketplace_item` 的输入模式。"""
    title: str = Field(description="商品标题，不超过64字")
    description: str = Field(default="", description="商品详细描述")
    price: float | None = Field(default=None, description="售价")
    original_price: float | None = Field(default=None, description="原价（可选）")
    condition: str = Field(default="九成新", description="新旧程度：全新|95新|九成新|八成新|六成新")
    category: str = Field(default="其他", description="商品分类：教材|数码|外设|生活|出行|考研|其他")

# === Middleware ===
class YanyuMiddleware(AgentMiddleware[AgentState, ContextT, ResponseT]):
    """提供与公告板智能体（雁羽-鸿络 Yanyu-Flux）直接交互的原生工具的中间件。"""

    def __init__(self, *, workspace_root: str) -> None:
        self.workspace_root = workspace_root
        # 从 workspace_root 推导 account 目录: .../accounts/<user>/workspace -> .../accounts/<user>
        self._account_dir = Path(workspace_root).parent
        self.tools: list[BaseTool] = [
            self._create_publish_info_tool(),
            self._create_list_infos_tool(),
            self._create_search_infos_tool(),
            self._create_follow_unfollow_info_tool(),
            self._create_list_skills_tool(),
            self._create_upload_skill_tool(),
            self._create_download_skill_tool(),
            self._create_search_marketplace_tool(),
            self._create_publish_marketplace_tool(),
            self._create_my_marketplace_tool(),
        ]

    def _get_config(self) -> dict[str, str]:
        """从用户的账户 config.toml 动态加载配置。"""
        cfg = {
            "user_id": "",
            "wit_aic": "",
            "skill_id": "yanyu",
            "flux_url": "http://127.0.0.1:13002",
            "wit_endpoint": "http://10.106.130.222:7020"
        }
        
        # 尝试在 account_dir 或 workspace_root 中查找 config.toml
        for path in [
            self._account_dir / "config.toml",
            Path(self.workspace_root) / "config.toml",
        ]:
            if path.exists():
                try:
                    import tomllib
                    with open(path, "rb") as f:
                        toml_data = tomllib.load(f)
                        if "services" in toml_data and "flux" in toml_data["services"]:
                            cfg["flux_url"] = toml_data["services"]["flux"].get("base_url", cfg["flux_url"])
                        if "identity" in toml_data:
                            cfg["wit_aic"] = toml_data["identity"].get("agent_aic", cfg["wit_aic"])
                            cfg["user_id"] = toml_data["identity"].get("user_id", cfg["user_id"])
                except Exception as e:
                    logger.warning("Failed to parse config.toml at %s: %s", path, e)

        # 检查环境变量
        env_flux = os.getenv("FLUX_ENDPOINT") or os.getenv("FLUX_URL")
        if env_flux:
            cfg["flux_url"] = env_flux

        cfg["flux_url"] = cfg["flux_url"].rstrip("/")
        return cfg

    def _get_auth_headers(self) -> dict[str, str]:
        """从工作空间或账户凭证中动态读取访问 Token。"""
        # 首先尝试在账户或工作空间中查找 credentials/flux.json
        for path in [
            self._account_dir / "credentials" / "flux.json",
            Path(self.workspace_root) / "credentials" / "flux.json",
        ]:
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        token = data.get("access_token")
                        if token:
                            return {"Authorization": f"Bearer {token}"}
                except Exception as e:
                    logger.warning("Failed to read OIDC token file %s: %s", path, e)

        # .kc_token 回退已废弃 — 统一从 credentials/flux.json 读取
        return {}

    # --- Tool implementation factory helpers ---

    def _create_publish_info_tool(self) -> BaseTool:
        """创建 `publish_info` 工具。"""
        async def async_publish_info(
            description: str, 
            extra: dict | None = None, 
            attachments: list[str] = []
        ) -> str:
            cfg = self._get_config()
            endpoint = f"{cfg['flux_url']}/infos"
            headers = self._get_auth_headers()

            # 设置默认过期时间（从现在起 7 天）
            from datetime import datetime, timedelta, timezone
            expire_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

            payload = {
                "aic": cfg["wit_aic"],
                "endpoint": cfg["wit_endpoint"],
                "description": description,
                "extra": extra or {},
                "expire_at": expire_at
            }

            files_to_send = []
            try:
                if attachments:
                    for file_path in attachments:
                        path = Path(file_path)
                        if path.exists():
                            mime, _ = mimetypes.guess_type(str(path))
                            mime = mime or "application/octet-stream"
                            file_data = open(path, "rb")
                            files_to_send.append(("files", (path.name, file_data, mime)))
                        else:
                            logger.warning("Attachment path not found: %s", file_path)

                    multipart_fields = {
                        "data": (None, json.dumps(payload, ensure_ascii=False), "application/json"),
                    }

                    def _post():
                        return requests.post(
                            endpoint,
                            headers=headers,
                            files=list(multipart_fields.items()) + files_to_send,
                            timeout=30,
                        )
                else:
                    def _post():
                        return requests.post(
                            endpoint,
                            headers=headers,
                            json=payload,
                            timeout=30,
                        )

                response = await asyncio.to_thread(_post)
                response.raise_for_status()
                data = response.json()
                return f"成功！公告已成功发布在雁羽-鸿络（Yanyu-Flux）平台上。\nID: {data.get('id')}\n状态: {data.get('status')}\n描述: {description}"
            except Exception as e:
                return f"在雁羽-鸿络（Yanyu-Flux）上发布公告时出错：{e}"
            finally:
                for _, file_tuple in files_to_send:
                    file_tuple[1].close()

        return StructuredTool.from_function(
            name="publish_info",
            description="在雁羽-鸿络（Yanyu-Flux）平台上发布或创建社交信息/活动公告（例如球赛/运动比赛、约会、交易、聚会），可选择添加附件。",
            func=lambda *a, **k: None,
            coroutine=async_publish_info,
            infer_schema=False,
            args_schema=PublishInfoSchema,
        )

    def _create_list_infos_tool(self) -> BaseTool:
        """创建 `list_infos` 工具。"""
        async def async_list_infos(list_type: str = "published", status: str | None = None) -> str:
            cfg = self._get_config()
            headers = self._get_auth_headers()

            if list_type == "following":
                endpoint = f"{cfg['flux_url']}/infos/following"
                params = {
                    "follower_user_id": cfg["user_id"],
                    "follower_agent_aic": cfg["wit_aic"]
                }
            else:
                endpoint = f"{cfg['flux_url']}/infos"
                params = {
                    "publisher_user_id": cfg["user_id"],
                    "publisher_agent_aic": cfg["wit_aic"]
                }
                if status:
                    params["status"] = status

            try:
                def _get():
                    return requests.get(endpoint, params=params, headers=headers, timeout=30)
                
                response = await asyncio.to_thread(_get)
                response.raise_for_status()
                data = response.json()
                
                cache_dir = Path(self.workspace_root) / "yanyu"
                cache_dir.mkdir(parents=True, exist_ok=True)
                cache_path = cache_dir / f"my_{list_type}_infos.json"
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                
                return f"共找到 {len(data)} 条结果，已缓存在 {cache_path}，请自行查看。"
            except Exception as e:
                return f"列出 {list_type} 公告时出错：{e}"

        return StructuredTool.from_function(
            name="list_infos",
            description="列出先前由你自己的 Agent 档案发布或关注/加入的活跃、归档、过期或已删除的社交消息/公告。",
            func=lambda *a, **k: None,
            coroutine=async_list_infos,
            infer_schema=False,
            args_schema=ListInfosSchema,
        )

    def _create_search_infos_tool(self) -> BaseTool:
        """创建 `search_infos` 工具。"""
        async def async_search_infos(
            query: str = "",
            status: str | None = "active",
            top_k: int = 10
        ) -> str:
            cfg = self._get_config()
            endpoint = f"{cfg['flux_url']}/search"
            headers = self._get_auth_headers()

            payload = {
                "query": query,
                "filters": {
                    "status": status or "active"
                },
                "top_k": top_k,
                "page": 1,
                "page_size": top_k,
                "viewer_user_id": cfg["user_id"],
                "viewer_agent_aic": cfg["wit_aic"]
            }

            try:
                def _post():
                    return requests.post(endpoint, json=payload, headers=headers, timeout=30)
                
                response = await asyncio.to_thread(_post)
                response.raise_for_status()
                data = response.json()
                
                cache_dir = Path(self.workspace_root) / "yanyu"
                cache_dir.mkdir(parents=True, exist_ok=True)
                cache_path = cache_dir / "search_infos.json"
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                
                hits = data.get("hits", [])
                return f"共找到 {len(hits)} 条结果，已缓存在 {cache_path}，请自行查看。"
            except Exception as e:
                return f"搜索平台活动时出错：{e}"

        return StructuredTool.from_function(
            name="search_infos",
            description="在平台目录中搜索与查询匹配的消息、赛事、活动和匹配信息。",
            func=lambda *a, **k: None,
            coroutine=async_search_infos,
            infer_schema=False,
            args_schema=SearchInfosSchema,
        )

    def _create_follow_unfollow_info_tool(self) -> BaseTool:
        """创建 `follow_unfollow_info` 工具。"""
        async def async_follow_unfollow_info(info_id: str, action: str) -> str:
            if action not in ("follow", "unfollow"):
                return "Error: Action must be either 'follow' or 'unfollow'."

            cfg = self._get_config()
            endpoint = f"{cfg['flux_url']}/infos/{info_id}/{action}"
            headers = self._get_auth_headers()

            if action == "follow":
                payload = {
                    "follower_user_id": cfg["user_id"],
                    "aic": cfg["wit_aic"],
                    "skill_id": cfg["skill_id"],
                    "endpoint": cfg["wit_endpoint"]
                }
            else:
                payload = {
                    "follower_user_id": cfg["user_id"],
                    "follower_agent_aic": cfg["wit_aic"]
                }

            try:
                def _post():
                    return requests.post(endpoint, json=payload, headers=headers, timeout=30)

                response = await asyncio.to_thread(_post)
                if response.status_code == 400:
                    try:
                        detail = response.json().get("detail")
                        if detail == "cannot_follow_own_info":
                            return "失败：你不能关注自己发布的活动。"
                    except Exception:
                        pass
                elif response.status_code == 409:
                    try:
                        detail = response.json().get("detail")
                        if detail == "not_following":
                            return "失败：你没有关注此活动，因此无法取消关注。"
                    except Exception:
                        pass

                response.raise_for_status()
                data = response.json()
                
                # 格式化成功响应
                followers = data.get("follow_agents") or []
                follower_count = len(followers)
                
                if action == "follow":
                    return f"成功！你已关注活动 '{info_id}'。当前关注者人数：{follower_count}。"
                else:
                    return f"成功！你已取消关注活动 '{info_id}'。当前关注者人数：{follower_count}。"

            except Exception as e:
                return f"对活动 {info_id} 执行 {action} 操作时出错：{e}"

        return StructuredTool.from_function(
            name="follow_unfollow_info",
            description="使用唯一 ID 在雁羽-鸿络（Yanyu-Flux）平台上关注（加入/报名）或取消关注（取消登记）某条消息/活动。",
            func=lambda *a, **k: None,
            coroutine=async_follow_unfollow_info,
            infer_schema=False,
            args_schema=FollowUnfollowInfoSchema,
        )

    def _create_list_skills_tool(self) -> BaseTool:
        """创建 `list_skills` 工具。"""
        async def async_list_skills(only_mine: bool = False) -> str:
            cfg = self._get_config()
            headers = self._get_auth_headers()
            
            endpoint = f"{cfg['flux_url']}/skill-packages/mine" if only_mine else f"{cfg['flux_url']}/skill-packages"

            try:
                def _get():
                    return requests.get(endpoint, headers=headers, timeout=30)
                
                response = await asyncio.to_thread(_get)
                response.raise_for_status()
                data = response.json()
                
                cache_dir = Path(self.workspace_root) / "yanyu"
                cache_dir.mkdir(parents=True, exist_ok=True)
                cache_path = cache_dir / "skills.json"
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                
                items = data.get("items") if isinstance(data, dict) else data
                count = len(items) if isinstance(items, list) else 0
                return f"共找到 {count} 条结果，已缓存在 {cache_path}，请自行查看。"
            except Exception as e:
                return f"列出技能包时出错：{e}"

        return StructuredTool.from_function(
            name="list_skills",
            description="列出平台上可用的技能包，并提供仅过滤自己上传的技能包的选项。",
            func=lambda *a, **k: None,
            coroutine=async_list_skills,
            infer_schema=False,
            args_schema=ListSkillsSchema,
        )

    def _create_upload_skill_tool(self) -> BaseTool:
        """创建 `upload_skill` 工具。"""
        async def async_upload_skill(package_name: str, zip_path: str) -> str:
            cfg = self._get_config()
            endpoint = f"{cfg['flux_url']}/skill-packages/upload"
            headers = self._get_auth_headers()

            path = Path(zip_path)
            if not path.exists():
                return f"错误：本地 ZIP 文件在以下路径不存在：{zip_path}"

            files = {
                "package": (path.name, open(path, "rb"), "application/zip")
            }
            data = {
                "package_name": package_name
            }

            try:
                def _post():
                    return requests.post(endpoint, headers=headers, data=data, files=files, timeout=60)
                
                response = await asyncio.to_thread(_post)
                if response.status_code == 409:
                    try:
                        detail = response.json().get("detail")
                        if detail == "package_name_already_exists":
                            return f"失败：平台上已存在名为 '{package_name}' 的技能包。"
                    except Exception:
                        pass
                
                response.raise_for_status()
                res_data = response.json()
                
                pkg_id = None
                status = "pending"
                if isinstance(res_data, dict):
                    items = res_data.get("items")
                    if items and isinstance(items, list) and items:
                        pkg_id = items[0].get("id")
                        status = items[0].get("status", "pending")
                    else:
                        pkg_id = res_data.get("id")
                        status = res_data.get("status", "pending")
                elif isinstance(res_data, list) and res_data:
                    pkg_id = res_data[0].get("id")
                    status = res_data[0].get("status", "pending")

                return f"成功！技能包 '{package_name}' 上传成功。\n技能包 ID: {pkg_id}\n状态: {status}"
            except Exception as e:
                return f"上传技能包时出错：{e}"
            finally:
                files["package"][1].close()

        return StructuredTool.from_function(
            name="upload_skill",
            description="上传技能包 ZIP 文件到平台。ZIP 包的根目录下必须包含 skill.md。",
            func=lambda *a, **k: None,
            coroutine=async_upload_skill,
            infer_schema=False,
            args_schema=UploadSkillSchema,
        )

    def _create_download_skill_tool(self) -> BaseTool:
        """创建 `download_skill` 工具。"""
        async def async_download_skill(package_id: str) -> str:
            import zipfile
            import shutil

            cfg = self._get_config()
            headers = self._get_auth_headers()
            
            # 第一步: 通过列表查询技能包名称
            package_name = None
            try:
                def _get_mine():
                    return requests.get(f"{cfg['flux_url']}/skill-packages/mine", headers=headers, timeout=10)
                r = await asyncio.to_thread(_get_mine)
                if r.status_code == 200:
                    items = r.json().get("items") if isinstance(r.json(), dict) else r.json()
                    for item in items:
                        if item.get("id") == package_id:
                            package_name = item.get("package_name")
                            break
                
                if not package_name:
                    def _get_all():
                        return requests.get(f"{cfg['flux_url']}/skill-packages", headers=headers, timeout=10)
                    r = await asyncio.to_thread(_get_all)
                    if r.status_code == 200:
                        items = r.json().get("items") if isinstance(r.json(), dict) else r.json()
                        for item in items:
                            if item.get("id") == package_id:
                                package_name = item.get("package_name")
                                break
            except Exception as e:
                logger.warning("Failed to lookup package_name by package_id: %s", e)

            target_name = package_name or package_id
            download_url = f"{cfg['flux_url']}/skill-packages/{package_id}/bundle"
            
            skills_dir = Path(self.workspace_root) / "skills"
            skills_dir.mkdir(parents=True, exist_ok=True)
            
            temp_zip = skills_dir / f"{package_id}_temp.zip"
            target_unzip_dir = skills_dir / target_name

            try:
                def _download():
                    return requests.get(download_url, headers=headers, timeout=60)
                
                response = await asyncio.to_thread(_download)
                response.raise_for_status()
                
                temp_zip.write_bytes(response.content)
                
                if target_unzip_dir.exists():
                    shutil.rmtree(target_unzip_dir)
                target_unzip_dir.mkdir(parents=True, exist_ok=True)
                
                with zipfile.ZipFile(temp_zip, "r") as z:
                    z.extractall(target_unzip_dir)
                
                return f"成功！技能包 '{target_name}'（ID: {package_id}）已下载，成功解压到 '{target_unzip_dir.name}' 并安装成功。"
            except Exception as e:
                return f"下载或安装技能包时出错：{e}"
            finally:
                if temp_zip.exists():
                    temp_zip.unlink()

        return StructuredTool.from_function(
            name="download_skill",
            description="根据 ID 下载、解压技能包，并安装到本地用户技能目录中。",
            func=lambda *a, **k: None,
            coroutine=async_download_skill,
            infer_schema=False,
            args_schema=DownloadSkillSchema,
        )

    # --- Marketplace 工具 ---

    def _create_search_marketplace_tool(self) -> BaseTool:
        """创建 `search_marketplace` 工具 —— 搜索二手商品"""
        async def async_search_marketplace(
            query: str = "",
            category: str | None = None,
            min_price: float | None = None,
            max_price: float | None = None,
            sort_by: str = "created_at",
        ) -> str:
            cfg = self._get_config()
            endpoint = f"{cfg['flux_url']}/marketplace/items"
            params: dict[str, str | int] = {
                "q": query,
                "sort_by": sort_by,
                "sort_order": "desc",
                "page": 1,
                "page_size": 10,
            }
            if category:
                params["category"] = category
            if min_price is not None:
                params["min_price"] = int(min_price)
            if max_price is not None:
                params["max_price"] = int(max_price)

            try:
                headers = self._get_auth_headers()

                def _get():
                    qs_parts = []
                    for k, v in params.items():
                        if v is not None:
                            qs_parts.append(f"{k}={v}")
                    qs = "&".join(qs_parts)
                    url = f"{endpoint}?{qs}"
                    return requests.get(url, headers=headers, timeout=15)

                response = await asyncio.to_thread(_get)
                response.raise_for_status()
                data = response.json()

                items = data.get("items", [])
                total = data.get("total", 0)

                if not items:
                    return f"二手市场中没有找到与「{query}」相关的商品。试试其他关键词？"

                cache_dir = Path(self.workspace_root) / "yanyu"
                cache_dir.mkdir(parents=True, exist_ok=True)
                cache_path = cache_dir / "marketplace_search.json"
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

                lines = [f"二手市场中共找到 {total} 件商品，以下是前 {len(items)} 件：\n"]
                for i, it in enumerate(items, 1):
                    price_str = f"¥{it['price']}" if it.get("price") else "价格面议"
                    lines.append(
                        f"{i}. **{it['title']}** {price_str} {it.get('condition','')} "
                        f"[{it.get('category','')}] 卖家:{it.get('seller_username','')} "
                        f"(ID:{it['id']})"
                    )

                lines.append(f"\n详细数据已缓存至 {cache_path}")
                return "\n".join(lines)

            except Exception as e:
                return f"搜索二手市场时出错：{e}"

        return StructuredTool.from_function(
            name="search_marketplace",
            description="在雁羽二手市场（Yanyu Marketplace）中搜索商品。可以按关键词、分类、价格范围搜索。",
            func=lambda *a, **k: None,
            coroutine=async_search_marketplace,
            infer_schema=False,
            args_schema=SearchMarketplaceSchema,
        )

    def _create_publish_marketplace_tool(self) -> BaseTool:
        """创建 `publish_marketplace_item` 工具 —— 发布二手商品"""
        async def async_publish_marketplace_item(
            title: str,
            description: str = "",
            price: float | None = None,
            original_price: float | None = None,
            condition: str = "九成新",
            category: str = "其他",
        ) -> str:
            cfg = self._get_config()
            endpoint = f"{cfg['flux_url']}/marketplace/items"
            headers = self._get_auth_headers()

            payload = {
                "title": title,
                "description": description,
                "price": price,
                "original_price": original_price,
                "condition": condition,
                "category": category,
            }

            try:
                def _post():
                    return requests.post(endpoint, json=payload, headers=headers, timeout=30)

                response = await asyncio.to_thread(_post)
                if response.status_code == 401:
                    return "发布失败：请先登录 flux 平台（需要先完成 Keycloak OIDC 认证）。"

                response.raise_for_status()
                data = response.json()
                return (
                    f"二手商品发布成功！\n"
                    f"标题：{data.get('title')}\n"
                    f"价格：{data.get('price')}\n"
                    f"分类：{data.get('category')}\n"
                    f"状态：{data.get('status')}\n"
                    f"ID：{data.get('id')}"
                )
            except Exception as e:
                return f"发布二手商品时出错：{e}"

        return StructuredTool.from_function(
            name="publish_marketplace_item",
            description="在雁羽二手市场（Yanyu Marketplace）上发布一件二手商品。需要提供标题、描述、价格等信息。",
            func=lambda *a, **k: None,
            coroutine=async_publish_marketplace_item,
            infer_schema=False,
            args_schema=PublishMarketplaceSchema,
        )

    def _create_my_marketplace_tool(self) -> BaseTool:
        """创建 `my_marketplace_items` 工具 —— 查看我发布的商品"""
        async def async_my_marketplace_items() -> str:
            cfg = self._get_config()
            endpoint = f"{cfg['flux_url']}/marketplace/my/items"
            headers = self._get_auth_headers()

            try:
                def _get():
                    return requests.get(endpoint, headers=headers, timeout=15)

                response = await asyncio.to_thread(_get)
                if response.status_code == 401:
                    return "请先登录 flux 平台。"

                response.raise_for_status()
                data = response.json()
                items = data.get("items", [])

                if not items:
                    return "你还没有发布任何二手商品。说「我要卖东西」来发布第一件！"

                cache_dir = Path(self.workspace_root) / "yanyu"
                cache_dir.mkdir(parents=True, exist_ok=True)
                cache_path = cache_dir / "my_marketplace_items.json"
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

                lines = [f"你共发布了 {data.get('total', 0)} 件商品：\n"]
                for i, it in enumerate(items, 1):
                    price_str = f"¥{it['price']}" if it.get("price") else "价格面议"
                    lines.append(
                        f"{i}. **{it['title']}** {price_str} "
                        f"状态:{it.get('status','')} 浏览:{it.get('view_count',0)}次"
                    )
                return "\n".join(lines)
            except Exception as e:
                return f"查看我的商品时出错：{e}"

        return StructuredTool.from_function(
            name="my_marketplace_items",
            description="查看我在雁羽二手市场上发布的所有商品。",
            func=lambda *a, **k: None,
            coroutine=async_my_marketplace_items,
        )

    # --- Hooks & Skills Loading ---

    async def abefore_agent(self, state: AgentState, runtime: object) -> dict[str, Any] | None:
        """从本地目录和雁羽-鸿络（Yanyu-Flux）平台加载并合并已批准的技能。"""
        if "yanyu_skills" in state:
            return None

        def _fetch_combined():
            cfg = self._get_config()
            headers = self._get_auth_headers()
            skills_dir = Path(self.workspace_root) / "skills"

            # 1. 加载本地技能
            local_skills = _list_local_skills(skills_dir)
            
            skills_by_name = {}
            for ls in local_skills:
                skills_by_name[ls["name"]] = {
                    "name": ls["name"],
                    "description": ls["description"],
                    "source": "local",
                    "frontmatter": ls.get("frontmatter", {})
                }

            # 2. 获取平台技能目录
            catalog_endpoint = f"{cfg['flux_url']}/skill-packages"
            try:
                response = requests.get(catalog_endpoint, headers=headers, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    items = data.get("items", []) if isinstance(data, dict) else data
                    for item in items:
                        name = item.get("package_name")
                        if not name:
                            continue
                        if name in skills_by_name:
                            skills_by_name[name]["source"] = "both"
                        else:
                            skills_by_name[name] = {
                                "name": name,
                                "description": f"雁羽-鸿络（Yanyu-Flux）上已批准的技能包 '{name}'。",
                                "source": "platform",
                                "frontmatter": {}
                            }
                else:
                    logger.warning("Flux skill-packages returned non-200 code: %d", response.status_code)
            except Exception as e:
                logger.warning("Failed to fetch skill-packages catalog from platform: %s", e)

            return list(skills_by_name.values())

        combined_skills = await asyncio.to_thread(_fetch_combined)
        return {"yanyu_skills": combined_skills}

    def before_agent(self, state: AgentState, runtime: object) -> dict[str, Any] | None:
        raise RuntimeError("Sync execution is not supported. Use the async version.")

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        raise RuntimeError("Sync execution is not supported. Use the async version.")

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[
            [ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]
        ],
    ) -> ModelResponse[ResponseT]:
        """将雁羽工具和系统提示词注入到模型调用中。"""
        # 1. Inject yanyu tools safely
        existing_names = {t.name for t in request.tools}
        merged_tools = list(request.tools)
        for tool in self.tools:
            if tool.name not in existing_names:
                merged_tools.append(tool)
        request = request.override(tools=merged_tools)

        # 2. Inject yanyu platform and skills prompt
        request = _inject_yanyu_prompt(request)

        return await handler(request)


# === Helpers for Skills parsing ===

MAX_SKILL_FILE_SIZE = 10 * 1024 * 1024

def _parse_skill_frontmatter(file_path: Path) -> dict | None:
    """从技能 markdown 文件中解析 YAML 前导数据，匹配 skills.py 的解析逻辑。"""
    try:
        if file_path.stat().st_size > MAX_SKILL_FILE_SIZE:
            logger.warning("Skipping %s: content too large", file_path)
            return None

        content = file_path.read_text(encoding="utf-8")
        
        # 匹配 --- 分隔符之间的 YAML 前导数据
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if not match:
            logger.warning("Skipping %s: no valid YAML frontmatter found", file_path)
            return None

        try:
            data = yaml.safe_load(match.group(1))
        except Exception as e:
            logger.warning("Invalid YAML in %s: %s", file_path, e)
            return None

        if not isinstance(data, dict):
            logger.warning("Skipping %s: frontmatter is not a mapping", file_path)
            return None

        name = str(data.get("name", "")).strip()
        description = str(data.get("description", "")).strip()

        if not name or not description:
            logger.warning("Skipping %s: missing required 'name' or 'description'", file_path)
            return None

        # 验证名称是否与目录名称匹配
        from agent.middlewares.skills import _validate_skill_name
        is_valid, error = _validate_skill_name(name, file_path.parent.name)
        if not is_valid:
            logger.warning("Skill '%s' in %s: %s", name, file_path, error)
            return None

        return {
            "name": name,
            "description": description,
            "frontmatter": data
        }
    except Exception as e:
        logger.warning("Failed to parse skill in %s: %s", file_path, e)
        return None


def _list_local_skills(skills_dir: Path) -> list[dict]:
    """扫描工作空间技能目录以查找本地技能。"""
    skills: list[dict] = []
    
    if not skills_dir.exists():
        try:
            skills_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning("Failed to create skills directory: %s", e)
            return []

    for item in skills_dir.iterdir():
        if not item.is_dir():
            continue
        
        md_file = None
        for filename in ("SKILL.md", "skill.md"):
            candidate = item / filename
            if candidate.is_file():
                md_file = candidate
                break
        
        if not md_file:
            continue

        skill_meta = _parse_skill_frontmatter(md_file)
        if skill_meta:
            skills.append(skill_meta)

    return skills


def _inject_yanyu_prompt(request: ModelRequest[ContextT]) -> ModelRequest[ContextT]:
    """将平台上下文、可用技能和能力指南注入系统消息。"""
    skills = request.state.get("yanyu_skills", [])
    
    if not skills:
        skills_list = "(暂无可用技能。)"
    else:
        lines = []
        for s in skills:
            src_str = ""
            if s["source"] == "local":
                src_str = " (本地)"
            elif s["source"] == "platform":
                src_str = " (平台目录)"
            lines.append(f"- **{s['name']}**{src_str}: {s['description']}")
        skills_list = "\n".join(lines)

    yanyu_prompt = f"""
## 公告板智能体 (雁羽-鸿络 Bulletin Board Agent) 集成与功能

你是一个专属的个人助手 Agent，与 **公告板智能体 (雁羽-鸿络 Bulletin Board Agent / Yanyu-Flux)** 公共信息空间进行了独特集成。

### 你的核心功能：
1. **信息发布与撮合平台 (Information Publish & Matchmaking)**：你可以在平台上发布/创建消息/活动/意图 (`publish_info`)、搜索平台活动/消息 (`search_infos`)、列出已发布或已关注/加入的消息/活动 (`list_infos`)，以及关注或取消关注消息/活动 (`follow_unfollow_info`)。
2. **智能体技能市场 (Agent Skill Market)**：你可以列出技能包 (`list_skills`)、上传本地技能包 (`upload_skill`)，以及下载/安装技能包 (`download_skill`)。

### 可用技能：
每个技能都定义了一个独特的能力包（包含 Markdown 格式的可执行脚本和描述元数据）。
{skills_list}

### 回答能力咨询（重要）：
如果用户询问关于 **你能做什么**、**你的能力是什么** 或 **你支持哪些功能**，你必须：
- 自豪地解释你是与 **公告板智能体 (雁羽-鸿络 Bulletin Board Agent / Yanyu-Flux)**（拥有技能市场和信息发布/撮合平台）集成的个人助手 Agent。
- 明确声明你可以帮助他们 **发布消息 or 交易/社交/活动意图**、**搜索活跃信息**、**关注/加入活动**，以及 **管理 Agent 技能**（上传、下载、列出）。
- 结合上面列出的具体可用技能来描述你的能力。
- 鼓励他们通过列出技能或搜索/发布信息来开始！
"""

    existing = request.system_message
    if existing is None:
        new_sys = SystemMessage(content=yanyu_prompt)
    else:
        old_content = existing.content if isinstance(existing.content, str) else str(existing.content)
        new_sys = SystemMessage(content=f"{old_content}\n\n{yanyu_prompt}")

    return request.override(system_message=new_sys)


__all__ = ["YanyuMiddleware"]
