# pylint: disable=invalid-name
import logging
import os
import json
import yaml

from django.contrib.auth.models import Group, Permission, User
from django_testscenarios.ubertest import TestCase
from lava_scheduler_app.dbutils import testjob_submission
from lava_scheduler_app.models import (
    Alias,
    Device,
    DeviceType,
    DevicesUnavailableException,
    Tag,
    TestJob,
)


# pylint gets confused with TestCase
# pylint: disable=no-self-use,invalid-name,too-many-ancestors,too-many-public-methods


class ModelFactory:
    def __init__(self):
        self._int = 0

    def getUniqueInteger(self):  # pylint: disable=invalid-name
        self._int += 1
        return self._int

    def getUniqueString(self, prefix="generic"):  # pylint: disable=invalid-name
        return "%s-%d" % (prefix, self.getUniqueInteger())

    def get_unique_user(self, prefix="generic"):  # pylint: disable=no-self-use
        return "%s-%d" % (prefix, User.objects.count() + 1)

    def cleanup(self):  # pylint: disable=no-self-use
        DeviceType.objects.all().delete()
        # make sure the DB is in a clean state wrt devices and jobs
        Device.objects.all().delete()
        TestJob.objects.all().delete()
        User.objects.all().delete()
        Group.objects.all().delete()

    def ensure_user(self, username, email, password):  # pylint: disable=no-self-use
        if User.objects.filter(username=username):
            user = User.objects.get(username=username)
        else:
            user = User.objects.create_user(username, email, password)
            user.save()
        return user

    def make_user(self):
        return User.objects.create_user(
            self.get_unique_user(),
            "%s@mail.invalid" % (self.getUniqueString(),),
            self.getUniqueString(),
        )

    def make_group(self, name=None):
        if name is None:
            name = self.getUniqueString("name")
        return Group.objects.get_or_create(name=name)[0]

    def ensure_device_type(self, name=None):
        if name is None:
            name = self.getUniqueString("name")
        logging.debug("asking for a device_type with name %s", name)
        device_type = DeviceType.objects.get_or_create(name=name)[0]
        self.make_device(device_type)
        return device_type

    def make_device_type(self, name=None):
        if name is None:
            name = self.getUniqueString("name")
        device_type, _ = DeviceType.objects.get_or_create(name=name)
        logging.debug("asking for a device of type %s", device_type.name)
        return device_type

    def make_device_type_alias(self, dt, name=None):
        if name is None:
            name = self.getUniqueString("name")
        alias, _ = Alias.objects.get_or_create(name=name, device_type=dt)
        logging.debug("asking for alias %s for device type %s", name, dt.name)
        return alias

    def ensure_tag(self, name):  # pylint: disable=no-self-use
        return Tag.objects.get_or_create(name=name)[0]

    def make_device(self, device_type=None, hostname=None, tags=None, **kw):
        if device_type is None:
            device_type = self.ensure_device_type()
        if hostname is None:
            hostname = self.getUniqueString()
        if not isinstance(tags, list):
            tags = []
        device = Device(
            device_type=device_type, state=Device.STATE_IDLE, hostname=hostname, **kw
        )
        device.tags.set(tags)
        logging.debug(
            "making a device of type %s %s with tags '%s'",
            device_type,
            device.hostname,
            ", ".join([x.name for x in device.tags.all()]),
        )
        device.save()
        return device

    def make_job_data(self, actions=None, **kw):
        if not actions:
            actions = []
        data = {"actions": actions}
        data.update(kw)
        if "target" not in data and "device_type" not in data:
            if DeviceType.objects.all():
                data["device_type"] = DeviceType.objects.all()[0].name
            else:
                device_type = self.ensure_device_type()
                self.make_device(device_type)
                data["device_type"] = device_type.name
        return data

    def make_job_yaml(self, **kw):
        return yaml.safe_dump(self.make_job_data(**kw))

    def make_job_data_from_file(self, sample_job_file):
        sample_job_file = os.path.join(
            os.path.dirname(__file__), "sample_jobs", sample_job_file
        )
        with open(sample_job_file, "r") as test_support:
            data = test_support.read()
        return data


class TestCaseWithFactory(TestCase):  # pylint: disable=too-many-ancestors
    def setUp(self):
        TestCase.setUp(self)
        self.factory = ModelFactory()


class TestTestJob(
    TestCaseWithFactory
):  # pylint: disable=too-many-ancestors,too-many-public-methods
    def test_preserve_comments(self):
        """
        TestJob.original_definition must preserve comments, if supplied.
        """
        definition = self.factory.make_job_data_from_file(
            "qemu-pipeline-first-job.yaml"
        )
        for line in definition:
            if line.startswith("#"):
                break
            self.fail("Comments have not been preserved")
        dt = self.factory.make_device_type(name="qemu")
        device = self.factory.make_device(device_type=dt, hostname="qemu-1")
        user = self.factory.make_user()
        job = TestJob.from_yaml_and_user(definition, user)
        job.refresh_from_db()
        self.assertEqual(user, job.submitter)
        for line in job.original_definition.split():
            if line.startswith("#"):
                break
            self.fail("Comments have not been preserved after submission")

    def test_user_permission(self):
        user = self.factory.make_user()
        admin_perm = Permission.objects.get(codename="admin_device")
        self.assertEqual("lava_scheduler_app", admin_perm.content_type.app_label)
        self.assertIsNotNone(admin_perm)
        self.assertEqual(admin_perm.name, "Can admin device")
        user.user_permissions.add(admin_perm)
        user.save()
        user = User.objects.get(username=user.username)
        self.assertEqual(
            {u"lava_scheduler_app.admin_device"}, user.get_all_permissions()
        )
        self.assertTrue(user.has_perm("lava_scheduler_app.admin_device"))

    def test_json_yaml(self):
        self.factory.cleanup()
        user = self.factory.make_user()
        dt = self.factory.make_device_type(name="qemu")
        device = self.factory.make_device(device_type=dt, hostname="qemu-1")
        device.save()
        definition = self.factory.make_job_data_from_file(
            "qemu-pipeline-first-job.yaml"
        )
        # convert content of file to JSON string
        json_def = json.dumps(yaml.safe_load(definition))
        job = testjob_submission(json_def, user, None)
        # check that submitted JSON is now YAML
        self.assertRaises(json.decoder.JSONDecodeError, json.loads, job.definition)
        yaml.safe_load(job.definition)
        self.assertIsInstance(job.definition, str)

    def test_job_data(self):
        self.factory.cleanup()
        user = self.factory.make_user()
        dt = self.factory.make_device_type(name="qemu")
        device = self.factory.make_device(device_type=dt, hostname="qemu-1")
        device.save()
        definition = self.factory.make_job_data_from_file(
            "qemu-pipeline-first-job.yaml"
        )
        job = testjob_submission(definition, user, None)
        data = job.create_job_data()
        self.assertIsNotNone(data)
        self.assertIn("start_time", data)
        # job has not started but job_data explicitly turns start_time into str()
        self.assertEqual(data["start_time"], "None")
        self.assertEqual(
            data["state_string"], TestJob.STATE_CHOICES[TestJob.STATE_SUBMITTED][1]
        )

    def test_device_type_alias(self):
        self.factory.cleanup()
        user = self.factory.make_user()
        user.user_permissions.add(Permission.objects.get(codename="add_testjob"))
        user.save()
        dt = self.factory.make_device_type(name="qemu")
        device = self.factory.make_device(device_type=dt, hostname="qemu-1")
        device.save()
        alias_name = "qemu_foo"
        alias = self.factory.make_device_type_alias(dt, name=alias_name)
        definition = self.factory.make_job_data_from_file("qemu-foo.yaml")
        job = testjob_submission(definition, user, None)
        self.assertEqual(job.requested_device_type, dt)

    def test_nonexisting_device_type_alias(self):
        self.factory.cleanup()
        user = self.factory.make_user()
        user.user_permissions.add(Permission.objects.get(codename="add_testjob"))
        user.save()
        dt = self.factory.make_device_type(name="qemu")
        device = self.factory.make_device(device_type=dt, hostname="qemu-1")
        device.save()
        alias_name = "qemu_foo"
        alias = self.factory.make_device_type_alias(dt, name=alias_name)
        definition = self.factory.make_job_data_from_file("qemu-foo-nonexisting.yaml")
        self.assertRaises(
            DevicesUnavailableException, testjob_submission, definition, user, None
        )
