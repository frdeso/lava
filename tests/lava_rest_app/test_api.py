# -*- coding: utf-8 -*-
# Copyright (C) 2018-2019 Linaro Limited
#
# Author: Milosz Wasilewski <milosz.wasilewski@linaro.org>
#
# This file is part of LAVA.
#
# LAVA is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License version 3
# as published by the Free Software Foundation
#
# LAVA is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with LAVA.  If not, see <http://www.gnu.org/licenses/>.

import csv
import json
import os
import pytest
import tap
import yaml

from django.conf import settings
from django.contrib.auth.models import User, Group
from django.urls import reverse
from rest_framework.test import APIClient

from lava_common.compat import yaml_safe_dump
from lava_scheduler_app.models import (
    Alias,
    Device,
    DeviceType,
    GroupDeviceTypePermission,
    Tag,
    TestJob,
    Worker,
)
from lava_results_app import models as result_models
from linaro_django_xmlrpc.models import AuthToken

from lava_rest_app import versions


EXAMPLE_JOB = """
job_name: test
visibility: public
timeouts:
  job:
    minutes: 10
  action:
    minutes: 5
actions: []
protocols: {}
"""

EXAMPLE_WORKING_JOB = """
device_type: public_device_type1
job_name: test
visibility: public
timeouts:
  job:
    minutes: 10
  action:
    minutes: 5
actions: []
protocols: {}
"""

LOG_FILE = """
- {"dt": "2018-10-03T16:28:28.199903", "lvl": "info", "msg": "lava-dispatcher, installed at version: 2018.7-1+stretch"}
- {"dt": "2018-10-03T16:28:28.200807", "lvl": "info", "msg": "start: 0 validate"}
"""


class TestRestApi:
    @pytest.fixture(autouse=True)
    def setUp(self, db):
        self.version = versions.versions[-1]  # use latest version by default

        # create users
        self.admin = User.objects.create(username="admin", is_superuser=True)
        self.admin_pwd = "supersecret"
        self.admin.set_password(self.admin_pwd)
        self.admin.save()
        self.user = User.objects.create(username="user1")
        self.user_pwd = "supersecret"
        self.user.set_password(self.user_pwd)
        self.user.save()
        self.user_no_token_pwd = "secret"
        self.user_no_token = User.objects.create(username="user2")
        self.user_no_token.set_password(self.user_no_token_pwd)
        self.user_no_token.save()

        self.group1 = Group.objects.create(name="group1")
        admintoken = AuthToken.objects.create(  # nosec - unit test support
            user=self.admin, secret="adminkey"
        )
        self.usertoken = AuthToken.objects.create(  # nosec - unit test support
            user=self.user, secret="userkey"
        )
        # create second token to check whether authentication still works
        AuthToken.objects.create(user=self.user, secret="userkey2")  # nosec - unittest

        self.userclient = APIClient()
        self.userclient.credentials(HTTP_AUTHORIZATION="Token " + self.usertoken.secret)
        self.userclient_no_token = APIClient()
        self.adminclient = APIClient()
        self.adminclient.credentials(HTTP_AUTHORIZATION="Token " + admintoken.secret)

        # Create workers
        self.worker1 = Worker.objects.create(
            hostname="worker1", state=Worker.STATE_ONLINE, health=Worker.HEALTH_ACTIVE
        )
        self.worker2 = Worker.objects.create(
            hostname="worker2",
            state=Worker.STATE_OFFLINE,
            health=Worker.HEALTH_MAINTENANCE,
        )

        # create devicetypes
        self.public_device_type1 = DeviceType.objects.create(name="public_device_type1")
        self.restricted_device_type1 = DeviceType.objects.create(
            name="restricted_device_type1"
        )
        GroupDeviceTypePermission.objects.assign_perm(
            DeviceType.VIEW_PERMISSION, self.group1, self.restricted_device_type1
        )
        self.invisible_device_type1 = DeviceType.objects.create(
            name="invisible_device_type1", display=False
        )

        Alias.objects.create(name="test1", device_type=self.public_device_type1)
        Alias.objects.create(name="test2", device_type=self.public_device_type1)
        Alias.objects.create(name="test3", device_type=self.invisible_device_type1)

        # create devices
        self.public_device1 = Device.objects.create(
            hostname="public01",
            device_type=self.public_device_type1,
            worker_host=self.worker1,
        )
        self.retired_device1 = Device.objects.create(
            hostname="retired01",
            device_type=self.public_device_type1,
            health=Device.HEALTH_RETIRED,
            worker_host=self.worker2,
        )

        # create testjobs
        self.public_testjob1 = TestJob.objects.create(
            definition=yaml_safe_dump(EXAMPLE_JOB),
            submitter=self.user,
            requested_device_type=self.public_device_type1,
        )
        self.private_testjob1 = TestJob.objects.create(
            definition=yaml_safe_dump(EXAMPLE_JOB),
            submitter=self.admin,
            requested_device_type=self.public_device_type1,
        )
        # create logs

        # create results for testjobs
        self.public_lava_suite = result_models.TestSuite.objects.create(
            name="lava", job=self.public_testjob1
        )
        self.public_test_case1 = result_models.TestCase.objects.create(
            name="foo",
            suite=self.public_lava_suite,
            result=result_models.TestCase.RESULT_FAIL,
        )
        self.public_test_case2 = result_models.TestCase.objects.create(
            name="bar",
            suite=self.public_lava_suite,
            result=result_models.TestCase.RESULT_PASS,
        )
        self.private_lava_suite = result_models.TestSuite.objects.create(
            name="lava", job=self.private_testjob1
        )
        self.private_test_case1 = result_models.TestCase.objects.create(
            name="foo",
            suite=self.private_lava_suite,
            result=result_models.TestCase.RESULT_FAIL,
        )
        self.private_test_case2 = result_models.TestCase.objects.create(
            name="bar",
            suite=self.private_lava_suite,
            result=result_models.TestCase.RESULT_PASS,
        )
        self.tag1 = Tag.objects.create(name="tag1", description="description1")
        self.tag2 = Tag.objects.create(name="tag2", description="description2")

    def hit(self, client, url):
        response = client.get(url)
        assert response.status_code == 200  # nosec - unit test support
        if hasattr(response, "content"):
            text = response.content.decode("utf-8")
            if response["Content-Type"] == "application/json":
                return json.loads(text)
            return text
        return ""

    def test_root(self):
        self.hit(self.userclient, reverse("api-root", args=[self.version]))

    def test_token(self):
        auth_dict = {
            "username": "%s" % self.user_no_token.get_username(),
            "password": self.user_no_token_pwd,
        }
        response = self.userclient_no_token.post(
            reverse("api-root", args=[self.version]) + "token/", auth_dict
        )
        assert response.status_code == 200  # nosec - unit test support
        text = response.content.decode("utf-8")
        assert "token" in json.loads(text).keys()  # nosec - unit test support

    def test_token_retrieval(self):
        auth_dict = {
            "username": "%s" % self.user.get_username(),
            "password": self.user_pwd,
        }
        response = self.userclient_no_token.post(
            reverse("api-root", args=[self.version]) + "token/", auth_dict
        )
        assert response.status_code == 200  # nosec - unit test support
        # response shouldn't cause exception. Below lines are just
        # additional check
        text = response.content.decode("utf-8")
        assert "token" in json.loads(text).keys()  # nosec - unit test support

    def test_testjobs(self):
        data = self.hit(
            self.userclient, reverse("api-root", args=[self.version]) + "jobs/"
        )
        assert len(data["results"]) == 1  # nosec - unit test support

    def test_testjobs_admin(self):
        data = self.hit(
            self.adminclient, reverse("api-root", args=[self.version]) + "jobs/"
        )
        assert len(data["results"]) == 2  # nosec - unit test support

    def test_testjob_item(self):
        self.hit(
            self.userclient,
            reverse("api-root", args=[self.version])
            + "jobs/%s/" % self.public_testjob1.id,
        )

    def test_testjob_logs(self, monkeypatch, tmpdir):
        (tmpdir / "output.yaml").write_text(LOG_FILE, encoding="utf-8")
        monkeypatch.setattr(TestJob, "output_dir", str(tmpdir))

        data = self.hit(
            self.userclient,
            reverse("api-root", args=[self.version])
            + "jobs/%s/logs/" % self.public_testjob1.id,
        )

    def test_testjob_logs_offset(self, monkeypatch, tmpdir):
        (tmpdir / "output.yaml").write_text(LOG_FILE, encoding="utf-8")
        monkeypatch.setattr(TestJob, "output_dir", str(tmpdir))

        # use start=2 as log lines count start from 1
        data = self.hit(
            self.userclient,
            reverse("api-root", args=[self.version])
            + "jobs/%s/logs/?start=2" % self.public_testjob1.id,
        )
        # the value below depends on the log fragment used
        # be careful when changing either the value below or the log fragment
        assert len(data) == 82  # nosec - unit test support

    def test_testjob_logs_offset_end(self, monkeypatch, tmpdir):
        (tmpdir / "output.yaml").write_text(LOG_FILE, encoding="utf-8")
        monkeypatch.setattr(TestJob, "output_dir", str(tmpdir))

        # use start=2 as log lines count start from 1
        data = self.hit(
            self.userclient,
            reverse("api-root", args=[self.version])
            + "jobs/%s/logs/?start=1&end=2" % self.public_testjob1.id,
        )
        # the value below depends on the log fragment used
        # be careful when changing either the value below or the log fragment
        assert len(data) == 120  # nosec - unit test support

    def test_testjob_logs_bad_offset(self, monkeypatch, tmpdir):
        (tmpdir / "output.yaml").write_text(LOG_FILE, encoding="utf-8")
        monkeypatch.setattr(TestJob, "output_dir", str(tmpdir))

        # use start=2 as log lines count start from 1
        response = self.userclient.get(
            reverse("api-root", args=[self.version])
            + "jobs/%s/logs/?start=2&end=1" % self.public_testjob1.id
        )
        assert response.status_code == 404  # nosec - unit test support

    def test_testjob_nologs(self):
        response = self.userclient.get(
            reverse("api-root", args=[self.version])
            + "jobs/%s/logs/" % self.public_testjob1.id
        )
        assert response.status_code == 404  # nosec - unit test support

    def test_testjob_suites(self):
        data = self.hit(
            self.userclient,
            reverse("api-root", args=[self.version])
            + "jobs/%s/suites/" % self.public_testjob1.id,
        )
        assert len(data["results"]) == 1  # nosec - unit test support

    # Testing the v0.1 base version 'tests' endpoint.
    def test_testjob_tests(self):
        data = self.hit(
            self.userclient,
            reverse("api-root", args=[versions.versions[0]])
            + "jobs/%s/tests/" % self.public_testjob1.id,
        )
        assert len(data["results"]) == 2  # nosec - unit test support

    def test_testjob_suite(self):
        data = self.hit(
            self.userclient,
            reverse("api-root", args=[self.version])
            + "jobs/%s/suites/%s/"
            % (self.public_testjob1.id, self.public_testjob1.testsuite_set.first().id),
        )
        assert (
            data["id"] == self.public_testjob1.testsuite_set.first().id
        )  # nosec - unit test support

    def test_testjob_suite_tests(self):
        data = self.hit(
            self.userclient,
            reverse("api-root", args=[self.version])
            + "jobs/%s/suites/%s/tests/"
            % (self.public_testjob1.id, self.public_testjob1.testsuite_set.first().id),
        )
        assert len(data["results"]) == 2  # nosec - unit test support

    def test_testjob_suite_test(self):
        data = self.hit(
            self.userclient,
            reverse("api-root", args=[self.version])
            + "jobs/%s/suites/%s/tests/%s/"
            % (
                self.public_testjob1.id,
                self.public_testjob1.testsuite_set.first().id,
                self.public_testjob1.testsuite_set.first().testcase_set.first().id,
            ),
        )
        assert (
            data["id"]
            == self.public_testjob1.testsuite_set.first().testcase_set.first().id
        )  # nosec - unit test support

    def test_testjob_metadata(self):
        data = self.hit(
            self.userclient,
            reverse("api-root", args=[self.version])
            + "jobs/%s/metadata/" % self.public_testjob1.id,
        )
        assert data["metadata"] == []  # nosec - unit test support

    def test_testjob_csv(self):
        data = self.hit(
            self.userclient,
            reverse("api-root", args=[self.version])
            + "jobs/%s/csv/" % self.public_testjob1.id,
        )
        csv_data = csv.reader(data.splitlines())
        assert list(csv_data)[1][0] == str(
            self.public_testjob1.id
        )  # nosec - unit test support

    def test_testjob_yaml(self):
        data = self.hit(
            self.userclient,
            reverse("api-root", args=[self.version])
            + "jobs/%s/yaml/" % self.public_testjob1.id,
        )
        data = yaml.load(data)
        assert data[0]["job"] == str(
            self.public_testjob1.id
        )  # nosec - unit test support

    def test_testjob_junit(self):
        data = self.hit(
            self.userclient,
            reverse("api-root", args=[self.version])
            + "jobs/%s/junit/" % self.public_testjob1.id,
        )
        lines = data.rstrip("\n").split("\n")
        assert len(lines) == 9  # nosec - unit test support
        assert (  # nosec - unit test support
            lines[0] == """<?xml version="1.0" encoding="utf-8"?>"""
        )
        assert (  # nosec - unit test support
            lines[1]
            == """<testsuites disabled="0" errors="1" failures="0" tests="2" time="0.0">"""
        )
        assert (  # nosec - unit test support
            lines[2]
            == """	<testsuite disabled="0" errors="1" failures="0" name="lava" skipped="0" tests="2" time="0">"""
        )
        assert lines[3].startswith(  # nosec - unit test support
            """		<testcase classname="lava" name="foo" timestamp="""
        )
        assert lines[3].endswith('">')  # nosec - unit test support
        assert (  # nosec - unit test support
            lines[4] == """			<error message="failed" type="error"/>"""
        )
        assert lines[5] == """		</testcase>"""  # nosec - unit test support
        assert lines[6].startswith(  # nosec - unit test support
            """		<testcase classname="lava" name="bar" timestamp="""
        )
        assert lines[6].endswith('"/>')  # nosec - unit test support
        assert lines[7] == """	</testsuite>"""  # nosec - unit test support
        assert lines[8] == """</testsuites>"""  # nosec - unit test support

    def test_testjob_tap13(self):
        data = self.hit(
            self.userclient,
            reverse("api-root", args=[self.version])
            + "jobs/%s/tap13/" % self.public_testjob1.id,
        )
        if hasattr(tap.tracker.Tracker, "set_plan"):
            assert (  # nosec - unit test support
                data
                == """TAP version 13
1..2
# TAP results for lava
not ok 1 foo
ok 2 bar
"""
            )
        else:
            assert (  # nosec - unit test support
                data
                == """# TAP results for lava
not ok 1 - foo
ok 2 - bar
"""
            )

    def test_testjob_suite_csv(self):
        data = self.hit(
            self.userclient,
            reverse("api-root", args=[self.version])
            + "jobs/%s/suites/%s/csv/"
            % (self.public_testjob1.id, self.public_testjob1.testsuite_set.first().id),
        )
        csv_data = csv.reader(data.splitlines())
        # Case id column is number 13 in a row.
        assert list(csv_data)[1][12] == str(
            self.public_testjob1.testsuite_set.first().testcase_set.first().id
        )  # nosec - unit test support

    def test_testjob_suite_yaml(self):
        data = self.hit(
            self.userclient,
            reverse("api-root", args=[self.version])
            + "jobs/%s/suites/%s/yaml/"
            % (self.public_testjob1.id, self.public_testjob1.testsuite_set.first().id),
        )
        data = yaml.load(data)
        assert (
            data[0]["suite"] == self.public_testjob1.testsuite_set.first().name
        )  # nosec - unit test support

    def test_devices(self):
        data = self.hit(
            self.userclient, reverse("api-root", args=[self.version]) + "devices/"
        )
        assert len(data["results"]) == 1  # nosec - unit test support

    def test_devices_admin(self):
        data = self.hit(
            self.adminclient, reverse("api-root", args=[self.version]) + "devices/"
        )
        assert len(data["results"]) == 1  # nosec - unit test support

    def test_devicetypes(self):
        data = self.hit(
            self.userclient, reverse("api-root", args=[self.version]) + "devicetypes/"
        )
        assert len(data["results"]) == 2  # nosec - unit test support

    def test_devicetypes_admin(self):
        data = self.hit(
            self.adminclient, reverse("api-root", args=[self.version]) + "devicetypes/"
        )
        assert len(data["results"]) == 3  # nosec - unit test support

    def test_devicetype_view(self):
        response = self.userclient.get(
            reverse("api-root", args=[self.version])
            + "devicetypes/%s/" % self.public_device_type1.name
        )
        assert response.status_code == 200  # nosec - unit test support

    def test_devicetype_view_unauthorized(self):
        response = self.userclient.get(
            reverse("api-root", args=[self.version])
            + "devicetypes/%s/" % self.restricted_device_type1.name
        )
        assert response.status_code == 404  # nosec - unit test support

    def test_devicetype_view_admin(self):
        response = self.adminclient.get(
            reverse("api-root", args=[self.version])
            + "devicetypes/%s/" % self.restricted_device_type1.name
        )
        assert response.status_code == 200  # nosec - unit test support

    def test_devicetype_create_unauthorized(self):
        response = self.userclient.post(
            reverse("api-root", args=[self.version]) + "devicetypes/", {"name": "bbb"}
        )
        assert response.status_code == 403  # nosec - unit test support

    def test_devicetype_create_no_name(self):
        response = self.adminclient.post(
            reverse("api-root", args=[self.version]) + "devicetypes/", {}
        )
        assert response.status_code == 400  # nosec - unit test support

    def test_devicetype_create(self):
        response = self.adminclient.post(
            reverse("api-root", args=[self.version]) + "devicetypes/", {"name": "bbb"}
        )
        assert response.status_code == 201  # nosec - unit test support

    def test_devicetype_get_template(self, monkeypatch, tmpdir):

        real_open = open
        (tmpdir / "qemu.jinja2").write_text("hello", encoding="utf-8")

        def monkey_open(path, *args):
            if path == os.path.join(settings.DEVICE_TYPES_PATH, "qemu.jinja2"):
                return real_open(str(tmpdir / "qemu.jinja2"), *args)
            if path == os.path.join(settings.DEVICE_TYPES_PATH, "bbb.jinja2"):
                raise FileNotFoundError()
            return real_open(path, *args)

        monkeypatch.setitem(__builtins__, "open", monkey_open)

        # 1. normal case
        qemu_device_type1 = DeviceType.objects.create(name="qemu")
        data = self.hit(
            self.userclient,
            reverse("api-root", args=[self.version])
            + "devicetypes/%s/template/" % qemu_device_type1.name,
        )
        data = yaml.load(data)
        assert data == str("hello")  # nosec

        # 2. Can't read the file
        bbb_device_type1 = DeviceType.objects.create(name="bbb")
        response = self.userclient.get(
            reverse("api-root", args=[self.version])
            + "devicetypes/%s/template/" % bbb_device_type1.name
        )
        assert response.status_code == 400  # nosec

    def test_devicetype_set_template(self, monkeypatch, tmpdir):
        real_open = open

        def monkey_open(path, *args):
            if path == os.path.join(settings.DEVICE_TYPES_PATH, "qemu.jinja2"):
                return real_open(str(tmpdir / "qemu.jinja2"), *args)
            if path == os.path.join(
                settings.DEVICE_TYPES_PATH, "public_device_type1.jinja2"
            ):
                raise OSError()
            return real_open(path, *args)

        monkeypatch.setitem(__builtins__, "open", monkey_open)

        # 1. normal case
        qemu_device_type1 = DeviceType.objects.create(name="qemu")
        response = self.adminclient.post(
            reverse("api-root", args=[self.version])
            + "devicetypes/%s/template/" % qemu_device_type1.name,
            {"template": "hello world"},
        )
        assert response.status_code == 204  # nosec - unit test support
        assert (tmpdir / "qemu.jinja2").read_text(
            encoding="utf-8"
        ) == "hello world"  # nosec

        # 2. Can't write the template
        response = self.adminclient.post(
            reverse("api-root", args=[self.version])
            + "devicetypes/%s/template/" % self.public_device_type1.name,
            {"template": "hello world"},
        )
        assert response.status_code == 400  # nosec

    def test_devicetype_get_health_check(self, monkeypatch, tmpdir):

        real_open = open
        (tmpdir / "qemu.yaml").write_text("hello", encoding="utf-8")

        def monkey_open(path, *args):
            if path == os.path.join(settings.HEALTH_CHECKS_PATH, "qemu.yaml"):
                return real_open(str(tmpdir / "qemu.yaml"), *args)
            if path == os.path.join(settings.HEALTH_CHECKS_PATH, "docker.yaml"):
                raise FileNotFoundError()
            return real_open(path, *args)

        monkeypatch.setitem(__builtins__, "open", monkey_open)

        # 1. normal case
        qemu_device_type1 = DeviceType.objects.create(name="qemu")
        data = self.hit(
            self.userclient,
            reverse("api-root", args=[self.version])
            + "devicetypes/%s/health_check/" % qemu_device_type1.name,
        )
        data = yaml.load(data)
        assert data == str("hello")  # nosec

        # 2. Can't read the health-check
        docker_device_type1 = DeviceType.objects.create(name="docker")
        response = self.userclient.get(
            reverse("api-root", args=[self.version])
            + "devicetypes/%s/health_check/" % docker_device_type1.name
        )
        assert response.status_code == 400  # nosec

    def test_devicetype_set_health_check(self, monkeypatch, tmpdir):
        real_open = open

        def monkey_open(path, *args):
            if path == os.path.join(settings.HEALTH_CHECKS_PATH, "qemu.yaml"):
                return real_open(str(tmpdir / "qemu.yaml"), *args)
            return real_open(path, *args)

        monkeypatch.setitem(__builtins__, "open", monkey_open)

        # 1. normal case
        qemu_device_type1 = DeviceType.objects.create(name="qemu")
        response = self.adminclient.post(
            reverse("api-root", args=[self.version])
            + "devicetypes/%s/health_check/" % qemu_device_type1.name,
            {"config": "hello world"},
        )
        assert response.status_code == 204  # nosec - unit test support
        assert (tmpdir / "qemu.yaml").read_text(
            encoding="utf-8"
        ) == "hello world"  # nosec

        # 2. Can't write the health-check
        response = self.adminclient.post(
            reverse("api-root", args=[self.version])
            + "devicetypes/%s/health_check/" % self.public_device_type1.name,
            {"config": "hello world"},
        )
        assert response.status_code == 400  # nosec

    def test_workers(self):
        data = self.hit(
            self.userclient,
            reverse("api-root", args=[self.version]) + "workers/?ordering=hostname",
        )
        # We get 3 workers because the default one (example.com) is always
        # created by the migrations
        assert len(data["results"]) == 3  # nosec - unit test support
        assert (  # nosec - unit test support
            data["results"][0]["hostname"] == "example.com"
        )
        assert data["results"][1]["hostname"] == "worker1"  # nosec - unit test support
        assert data["results"][1]["health"] == "Active"  # nosec - unit test support
        assert data["results"][1]["state"] == "Online"  # nosec - unit test support
        assert data["results"][2]["hostname"] == "worker2"  # nosec - unit test support
        assert (  # nosec - unit test support
            data["results"][2]["health"] == "Maintenance"
        )
        assert data["results"][2]["state"] == "Offline"  # nosec - unit test support

    def test_aliases_list(self):
        data = self.hit(
            self.userclient,
            reverse("api-root", args=[self.version]) + "aliases/?ordering=name",
        )
        # We have 3 aliases, but only 2 are visible
        assert len(data["results"]) == 2  # nosec - unit test support
        assert data["results"][0]["name"] == "test1"  # nosec - unit test support
        assert data["results"][1]["name"] == "test2"  # nosec - unit test support

    def test_aliases_retrieve(self):
        data = self.hit(
            self.userclient, reverse("api-root", args=[self.version]) + "aliases/test1/"
        )
        assert data["name"] == "test1"  # nosec - unit test support
        assert data["device_type"] == "public_device_type1"  # nosec - unit test support

    def test_aliases_create_unauthorized(self):
        response = self.userclient.post(
            reverse("api-root", args=[self.version]) + "aliases/",
            {"name": "test4", "device_type": "qemu"},
        )
        assert response.status_code == 403  # nosec - unit test support

    def test_aliases_create(self):
        response = self.adminclient.post(
            reverse("api-root", args=[self.version]) + "aliases/",
            {"name": "test4", "device_type": "public_device_type1"},
        )
        assert response.status_code == 201  # nosec - unit test support

    def test_aliases_delete_unauthorized(self):
        response = self.userclient.delete(
            reverse("api-root", args=[self.version]) + "aliases/test2/"
        )
        assert response.status_code == 403  # nosec - unit test support

    def test_aliases_delete(self):
        response = self.adminclient.delete(
            reverse("api-root", args=[self.version]) + "aliases/test2/"
        )
        assert response.status_code == 204  # nosec - unit test support

    def test_submit_unauthorized(self):
        response = self.userclient.post(
            reverse("api-root", args=[self.version]) + "jobs/",
            {"definition": EXAMPLE_JOB},
        )
        assert response.status_code == 400  # nosec - unit test support

    def test_submit_bad_request_no_device_type(self):
        response = self.adminclient.post(
            reverse("api-root", args=[self.version]) + "jobs/",
            {"definition": EXAMPLE_JOB},
            format="json",
        )
        assert response.status_code == 400  # nosec - unit test support
        content = json.loads(response.content.decode("utf-8"))
        assert (
            content["message"] == "job submission failed: 'device_type'."
        )  # nosec - unit test support

    def test_submit(self):
        response = self.adminclient.post(
            reverse("api-root", args=[self.version]) + "jobs/",
            {"definition": EXAMPLE_WORKING_JOB},
            format="json",
        )
        assert response.status_code == 201  # nosec - unit test support
        assert TestJob.objects.count() == 3  # nosec - unit test support

    def test_tags_list(self):
        data = self.hit(
            self.userclient,
            reverse("api-root", args=[self.version]) + "tags/?ordering=name",
        )
        assert len(data["results"]) == 2  # nosec - unit test support
        assert data["results"][0]["name"] == "tag1"  # nosec - unit test support
        assert data["results"][1]["name"] == "tag2"  # nosec - unit test support

    def test_tags_retrieve(self):
        data = self.hit(
            self.userclient,
            reverse("api-root", args=[self.version]) + "tags/%s/" % self.tag1.id,
        )
        assert data["name"] == "tag1"  # nosec - unit test support
        assert data["description"] == "description1"  # nosec - unit test support

    def test_tags_create_unauthorized(self):
        response = self.userclient.post(
            reverse("api-root", args=[self.version]) + "tags/",
            {"name": "tag3", "description": "description3"},
        )
        assert response.status_code == 403  # nosec - unit test support

    def test_tags_create(self):
        response = self.adminclient.post(
            reverse("api-root", args=[self.version]) + "tags/",
            {"name": "tag3", "description": "description3"},
        )
        assert response.status_code == 201  # nosec - unit test support

    def test_tags_delete_unauthorized(self):
        response = self.userclient.delete(
            reverse("api-root", args=[self.version]) + "tags/%s/" % self.tag2.id
        )
        assert response.status_code == 403  # nosec - unit test support

    def test_tags_delete(self):
        response = self.adminclient.delete(
            reverse("api-root", args=[self.version]) + "tags/%s/" % self.tag2.id
        )
        assert response.status_code == 204  # nosec - unit test support


def test_view_root(client):
    ret = client.get(reverse("api-root", args=[versions.versions[-1]]) + "?format=api")
    assert ret.status_code == 200


def test_view_devices(client, db):
    ret = client.get(
        reverse("api-root", args=[versions.versions[-1]]) + "devices/?format=api"
    )
    assert ret.status_code == 200


def test_view_devicetypes(client, db):
    ret = client.get(
        reverse("api-root", args=[versions.versions[-1]]) + "devicetypes/?format=api"
    )
    assert ret.status_code == 200


def test_view_jobs(client, db):
    ret = client.get(
        reverse("api-root", args=[versions.versions[-1]]) + "jobs/?format=api"
    )
    assert ret.status_code == 200


def test_view_workers(client, db):
    ret = client.get(
        reverse("api-root", args=[versions.versions[-1]]) + "workers/?format=api"
    )
    assert ret.status_code == 200
