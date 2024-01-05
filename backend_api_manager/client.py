from urllib.parse import urljoin

import httpx

from backend_api_manager.models import (
    HummingbotInstanceConfig,
    StartBotAction,
    StopBotAction,
)


class BackendAPIClient:
    _instance = None

    def __init__(self, base_url):
        self.base_url = base_url

    @classmethod
    def get_instance(cls, base_url: str = None):
        if cls._instance is None:
            if base_url is None:
                raise ValueError(
                    "Base URL and token must be provided for initialization"
                )
            cls._instance = cls(base_url)
        return cls._instance

    def get(self, path, params=None):
        return self._sync_request("GET", path, params=params)

    def post(self, path, data=None):
        return self._sync_request("POST", path, json=data)

    def put(self, path, data=None):
        return self._sync_request("PUT", path, json=data)

    def delete(self, path):
        return self._sync_request("DELETE", path)

    def _sync_request(self, method, path, **kwargs):
        url = urljoin(self.base_url, path)
        with httpx.Client() as client:
            response = client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()

    async def async_get(self, path, params=None):
        return await self._async_request("GET", path, params=params)

    async def async_post(self, path, data=None):
        return await self._async_request("POST", path, json=data)

    async def async_put(self, path, data=None):
        return await self._async_request("PUT", path, json=data)

    async def async_delete(self, path):
        return await self._async_request("DELETE", path)

    async def _async_request(self, method, path, **kwargs):
        url = urljoin(self.base_url, path)
        async with httpx.AsyncClient() as client:
            response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()

    async def async_get_image_tags(self, image_name):
        return await self._async_request("GET", f"/available-images/{image_name}")

    async def async_pull_image(self, image_name):
        return await self._async_request(
            "POST", "/pull-image/", json={"image_name": image_name}
        )

    async def async_list_scripts(self):
        return await self._async_request("GET", "/list-scripts")

    async def async_list_scripts_configs(self):
        return await self._async_request("GET", "/list-scripts-configs")

    async def async_list_credentials(self):
        return await self._async_request("GET", "/list-credentials")

    async def async_active_containers(self):
        return await self._async_request("GET", "/active-containers")

    async def async_exited_containers(self):
        return await self._async_request("GET", "/exited-containers")

    async def async_remove_container(
        self, container_name, archive_locally=True, s3_bucket=None
    ):
        return await self._async_request(
            "POST",
            f"/remove-container/{container_name}",
            params={"archive_locally": archive_locally, "s3_bucket": s3_bucket},
        )

    async def async_create_hummingbot_instance(self, config: HummingbotInstanceConfig):
        return await self._async_request(
            "POST", "/create-hummingbot-instance", json=config.dict()
        )

    async def async_start_bot(self, action: StartBotAction):
        return await self._async_request("POST", "/start-bot", json=action.dict())

    async def async_stop_bot(self, action: StopBotAction):
        return await self._async_request("POST", "/stop-bot", json=action.dict())

    async def async_get_bot_status(self, bot_name):
        return await self._async_request("GET", f"/get-bot-status/{bot_name}")

    async def async_get_bot_history(self, bot_name):
        return await self._async_request("GET", f"/get-bot-history/{bot_name}")

    def list_scripts(self):
        return self._sync_request("GET", "/list-scripts")

    def list_scripts_configs(self):
        return self._sync_request("GET", "/list-scripts-configs")

    def list_credentials(self):
        return self._sync_request("GET", "/list-credentials")

    def active_containers(self):
        return self._sync_request("GET", "/active-containers")

    def get_image_tags(self, image_name):
        return self._sync_request("GET", f"/available-images/{image_name}")

    def pull_image(self, image_name):
        return self._sync_request(
            "POST", "/pull-image/", json={"image_name": image_name}
        )

    def exited_containers(self):
        return self._sync_request("GET", "/exited-containers")

    def remove_container(self, container_name, archive_locally=True, s3_bucket=None):
        return self._sync_request(
            "POST",
            f"/remove-container/{container_name}",
            params={"archive_locally": archive_locally, "s3_bucket": s3_bucket},
        )

    def sync_create_hummingbot_instance(self, config: HummingbotInstanceConfig):
        return self._sync_request(
            "POST", "/create-hummingbot-instance", json=config.dict()
        )

    def sync_start_bot(self, action: StartBotAction):
        return self._sync_request("POST", "/start-bot", json=action.dict())

    def sync_stop_bot(self, action: StopBotAction):
        return self._sync_request("POST", "/stop-bot", json=action.dict())

    def get_bot_status(self, bot_name):
        return self._sync_request("GET", f"/get-bot-status/{bot_name}")

    def get_bot_history(self, bot_name):
        return self._sync_request("GET", f"/get-bot-history/{bot_name}")
