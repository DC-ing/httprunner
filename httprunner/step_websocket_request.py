# -*- coding: utf-8 -*-
import platform
import sys
import time
import json
from typing import Text, Union

from loguru import logger

from httprunner import utils
from httprunner.exceptions import ValidationFailure
from httprunner.models import (
    IStep,
    StepResult,
    TStep,
    WebsocketMethodEnum,
    TWebsocketRequest,
)
from httprunner.parser import build_url, parse_variables_mapping
from httprunner.response import WebsocketResponseObject
from httprunner.runner import ALLURE, HttpRunner
from httprunner.step_request import (
    StepRequestExtraction,
    StepRequestValidation,
    call_hooks,
    pretty_format,
)

try:
    import websocket

    WEBSOCKET_READY = True
except ModuleNotFoundError:
    WEBSOCKET_READY = False


def ensure_websocket_ready():
    if WEBSOCKET_READY:
        return

    msg = """
    uploader extension dependencies uninstalled, install first and try again.
    install with pip:
    $ pip install websocket-client

    or you can install httprunner with optional upload dependencies:
    $ pip install "httprunner[websocket]"
    """
    logger.error(msg)
    sys.exit(1)


def run_step_websocket_request(runner: HttpRunner, step: TStep) -> StepResult:
    """run:teststep:websocket request"""
    step_result = StepResult(
        name=step.name,
        step_type="websocket request",
        success=False,
        content_size=0,
    )
    start_time = time.time()

    # parse
    functions = runner.parser.functions_mapping
    step_variables = runner.merge_step_variables(step.variables)
    # parse variables
    step_variables = parse_variables_mapping(step_variables, functions)

    websocket_request_dict = step.websocket_request.dict()

    parsed_request_dict = runner.parser.parse_data(
        websocket_request_dict, step_variables
    )

    request_headers = parsed_request_dict.pop("headers", {})
    # omit pseudo header names for HTTP/1, e.g. :authority, :type, :path, :scheme
    request_headers = {
        key: request_headers[key] for key in request_headers if not key.startswith(":")
    }
    request_headers["HRUN-Websocket-Request-ID"] = (
        f"HRUN-{runner.case_id}-{str(int(time.time() * 1000))[-6:]}"
    )
    parsed_request_dict["headers"] = request_headers

    step_variables["websocket"] = parsed_request_dict

    # ensure websocket client exist
    if not runner.websocket_client:
        ensure_websocket_ready()
        from httprunner.websocket.websocket_client import WebsocketClient

        runner.websocket_client = WebsocketClient()

    # setup hooks
    if step.setup_hooks:
        call_hooks(runner, step.setup_hooks, step_variables, "setup websocket request")

    # prepare arguments
    config = runner.get_config()
    method_type = parsed_request_dict.pop("type")
    url_path = parsed_request_dict.pop("url")
    url = build_url(config.base_url, url_path)
    # parsed_request_dict["json"] = parsed_request_dict.pop("req_json", {})

    # log request
    request_print = "====== websocket request details ======\n"
    request_print += f"url: {url}\n"
    request_print += f"type: {method_type}\n"
    for k, v in parsed_request_dict.items():
        request_print += f"{k}: {pretty_format(v)}\n"

    logger.debug(request_print)
    if ALLURE is not None:
        ALLURE.attach(
            request_print,
            name="websocket request details",
            attachment_type=ALLURE.attachment_type.TEXT,
        )

    runner.websocket_client.send_request(
        method_type,
        url,
        header=parsed_request_dict.get("headers"),
        timeout=parsed_request_dict.get("timeout"),
        text=parsed_request_dict.get("text"),
        binary=parsed_request_dict.get("binary"),
        close_status=parsed_request_dict.get("close_status"),
    )

    # log response
    response_print = "====== websocket response details ======\n"
    response_print += f"status_code: {runner.websocket_client.client.status}\n"
    response_print += (
        f"headers: {pretty_format(runner.websocket_client.client.headers)}\n"
    )

    resp_body = runner.websocket_client.text

    response_print += f"body: {pretty_format(resp_body)}\n"
    logger.debug(response_print)
    if ALLURE is not None:
        ALLURE.attach(
            response_print,
            name="websocket response details",
            attachment_type=ALLURE.attachment_type.TEXT,
        )
    resp_obj = WebsocketResponseObject(runner.websocket_client, runner.parser)
    step_variables["response"] = resp_obj

    # teardown hooks
    if step.teardown_hooks:
        call_hooks(runner, step.teardown_hooks, step_variables, "teardown request")

    # extract
    extractors = step.extract
    extract_mapping = resp_obj.extract(extractors, step_variables)
    step_result.export_vars = extract_mapping

    variables_mapping = step_variables
    variables_mapping.update(extract_mapping)

    # validate
    validators = step.validators
    try:
        resp_obj.validate(validators, variables_mapping)
        step_result.success = True
    except ValidationFailure:
        step_result.success = False
        step_result.failure_info = resp_obj.failures_string
        # raise
    finally:
        session_data = runner.session.data
        session_data.success = step_result.success
        session_data.validators = resp_obj.validation_results

        # save step data
        step_result.data = session_data
        step_result.elapsed = time.time() - start_time

        return step_result


def ensure_websocket_method_type(type_str: str):
    if type_str == WebsocketMethodEnum.OPEN:
        return "open_connection"
    elif type_str == WebsocketMethodEnum.PING:
        return "ping_pong"
    elif type_str == WebsocketMethodEnum.WR:
        return "write_and_read"
    elif type_str == WebsocketMethodEnum.R:
        return "read"
    elif type_str == WebsocketMethodEnum.W:
        return "write"
    elif type_str == WebsocketMethodEnum.CLOSE:
        return "close_connection"
    else:
        raise ValueError("websocket type error")


class StepWebsocketRequestValidation(StepRequestValidation):
    def __init__(self, step: TStep):
        self.__step = step
        super().__init__(step)

    def run(self, runner: HttpRunner):
        return run_step_websocket_request(runner, self.__step)


class StepWebsocketRequestExtraction(StepRequestExtraction):
    def __init__(self, step: TStep):
        self.__step = step
        super().__init__(step)

    def run(self, runner: HttpRunner):
        return run_step_websocket_request(runner, self.__step)

    def validate(self) -> StepWebsocketRequestValidation:
        return StepWebsocketRequestValidation(self.__step)


class RunWebsocketRequest(IStep):
    def __init__(self, name: Text):
        self.__step = TStep(name=name)
        self.__step.websocket_request = TWebsocketRequest()

    def with_variables(self, **variables) -> "RunWebsocketRequest":
        self.__step.variables.update(variables)
        return self

    def with_retry(self, retry_times, retry_interval) -> "RunWebsocketRequest":
        self.__step.retry_times = retry_times
        self.__step.retry_interval = retry_interval
        return self

    def setup_hook(
        self, hook: Text, assign_var_name: Text = None
    ) -> "RunWebsocketRequest":
        if assign_var_name:
            self.__step.setup_hooks.append({assign_var_name: hook})
        else:
            self.__step.setup_hooks.append(hook)

        return self

    def with_headers(self, **headers) -> "RunWebsocketRequest":
        self.__step.websocket_request.headers.update(headers)
        return self

    def new_connection(self, new_connection) -> "RunWebsocketRequest":
        self.__step.websocket_request.new_connection = new_connection
        return self

    def with_timeout(self, timeout: float) -> "RunWebsocketRequest":
        self.__step.websocket_request.timeout = timeout
        return self

    def with_url(self, url) -> "RunWebsocketRequest":
        self.__step.websocket_request.url = url
        return self

    def with_text(self, text) -> "RunWebsocketRequest":
        self.__step.websocket_request.text = text
        return self

    def with_binary(self, binary) -> "RunWebsocketRequest":
        self.__step.websocket_request.binary = binary
        return self

    def with_close_status(self, close_status: float) -> "RunWebsocketRequest":
        self.__step.websocket_request.close_status = close_status
        return self

    def teardown_hook(
        self, hook: Text, assign_var_name: Text = None
    ) -> "RunWebsocketRequest":
        if assign_var_name:
            self.__step.teardown_hooks.append({assign_var_name: hook})
        else:
            self.__step.teardown_hooks.append(hook)
        return self

    def struct(self) -> TStep:
        return self.__step

    def name(self) -> Text:
        return self.__step.name

    def type(self) -> Text:
        return f"websocket-request-{self.__step.websocket_request.type}"

    def run(self, runner) -> StepResult:
        return run_step_websocket_request(runner, self.__step)

    def extract(self) -> StepWebsocketRequestExtraction:
        return StepWebsocketRequestExtraction(self.__step)

    def validate(self) -> StepWebsocketRequestValidation:
        return StepWebsocketRequestValidation(self.__step)

    def with_jmespath(
        self, jmes_path: Text, var_name: Text
    ) -> "StepWebsocketRequestExtraction":
        self.__step.extract[var_name] = jmes_path
        return StepWebsocketRequestExtraction(self.__step)

    def open_connection(self, url) -> "RunWebsocketRequest":
        self.__step.websocket_request.type = WebsocketMethodEnum.OPEN
        return self.with_url(url)

    def ping_pong(self, url) -> "RunWebsocketRequest":
        self.__step.websocket_request.type = WebsocketMethodEnum.PING
        return self.with_url(url)

    def write_and_read(self, url) -> "RunWebsocketRequest":
        self.__step.websocket_request.type = WebsocketMethodEnum.WR
        return self.with_url(url)

    def write(self, url) -> "RunWebsocketRequest":
        self.__step.websocket_request.type = WebsocketMethodEnum.W
        return self.with_url(url)

    def read(self, url) -> "RunWebsocketRequest":
        self.__step.websocket_request.type = WebsocketMethodEnum.R
        return self.with_url(url)

    def close_connection(self, url) -> "RunWebsocketRequest":
        self.__step.websocket_request.type = WebsocketMethodEnum.CLOSE
        return self.with_url(url)
