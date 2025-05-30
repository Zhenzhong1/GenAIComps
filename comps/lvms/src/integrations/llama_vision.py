# Copyright (C) 2024 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import json
import os
from typing import Union

import aiohttp
import requests

from comps import CustomLogger, LVMDoc, OpeaComponent, OpeaComponentRegistry, ServiceType, TextDoc

logger = CustomLogger("opea_llama_vision")
logflag = os.getenv("LOGFLAG", False)


@OpeaComponentRegistry.register("OPEA_LLAMA_VISION_LVM")
class OpeaLlamaVisionLvm(OpeaComponent):
    """A specialized LVM component derived from OpeaComponent for LLaMA-Vision services."""

    def __init__(self, name: str, description: str, config: dict = None):
        super().__init__(name, ServiceType.LVM.name.lower(), description, config)
        self.base_url = os.getenv("LVM_ENDPOINT", "http://localhost:9399")
        health_status = self.check_health()
        if not health_status:
            logger.error("specialized health check failed.")

    async def invoke(
        self,
        request: Union[LVMDoc],
    ) -> Union[TextDoc]:
        """Involve the LVM service to generate answer for the provided input."""
        if logflag:
            logger.info(request)

        inputs = {"image": request.image, "prompt": request.prompt, "max_new_tokens": request.max_new_tokens}
        # forward to the LLaMA Vision server
        async with aiohttp.ClientSession() as session:
            response = await session.post(url=f"{self.base_url}/v1/lvm", json=inputs, proxy=None)
            json_data = await response.json()
            result = json_data["text"]

            if logflag:
                logger.info(result)

        return TextDoc(text=result)

    def check_health(self) -> bool:
        """Checks the health of the embedding service.

        Returns:
            bool: True if the service is reachable and healthy, False otherwise.
        """
        try:
            response = requests.get(f"{self.base_url}/health")
            if response.status_code == 200:
                return True
            else:
                return False
        except Exception as e:
            # Handle connection errors, timeouts, etc.
            logger.error(f"Health check failed: {e}")
        return False
