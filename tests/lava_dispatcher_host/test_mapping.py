# Copyright (C) 2019 Linaro Limited
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from argparse import Namespace
import os
import pytest

from lava_common.compat import yaml_load
from lava_common.exceptions import InfrastructureError
import lava_dispatcher_host
from lava_dispatcher_host import add_device_container_mapping
from lava_dispatcher_host import share_device_with_container


@pytest.fixture(autouse=True)
def setup(monkeypatch, mocker, tmpdir):
    os.makedirs(tmpdir / "1")
    os.makedirs(tmpdir / "2")
    monkeypatch.setattr(lava_dispatcher_host, "JOBS_DIR", str(tmpdir))
    mocker.patch("os.path.exists", return_value=True)


def test_simple_mapping(tmpdir):
    device_info = {"foo": "bar"}
    add_device_container_mapping("1", device_info, "mycontainer")
    data = yaml_load(open(tmpdir / "1" / "usbmap.yaml"))

    assert data["device_info"] == device_info
    assert data["container"] == "mycontainer"
    assert data["container_type"] == "lxc"


def test_simple_share_device_with_container(mocker):
    check_call = mocker.patch("subprocess.check_call")
    add_device_container_mapping("1", {"serial_number": "1234567890"}, "mycontainer")
    share_device_with_container(Namespace(device="foo/bar", serial_number="1234567890"))

    check_call.assert_called_once_with(
        ["lxc-device", "-n", "mycontainer", "add", "/dev/foo/bar"]
    )


def test_mapping_with_serial_number_but_called_with_vendor_product_id_too(mocker):
    check_call = mocker.patch("subprocess.check_call")
    add_device_container_mapping(
        "1",
        {
            "serial_number": "1234567890",
            "vendor_id": None,
            "product_id": None,
            "fs_label": None,
        },
        "mycontainer",
    )
    share_device_with_container(
        Namespace(
            device="foo/bar",
            serial_number="1234567890",
            vendor_id="0123",
            product_id="4567",
        )
    )

    check_call.assert_called_once_with(
        ["lxc-device", "-n", "mycontainer", "add", "/dev/foo/bar"]
    )


def test_two_concurrent_jobs(mocker):
    check_call = mocker.patch("subprocess.check_call")
    add_device_container_mapping("1", {"serial_number": "1234567890"}, "container1")
    add_device_container_mapping("2", {"serial_number": "9876543210"}, "container2")
    share_device_with_container(Namespace(device="baz/qux", serial_number="9876543210"))

    check_call.assert_called_once_with(
        ["lxc-device", "-n", "container2", "add", "/dev/baz/qux"]
    )


def test_no_device_found(mocker):
    check_call = mocker.patch("subprocess.check_call")
    share_device_with_container(
        Namespace(device="bus/usb/001/099", serial_number="9876543210")
    )
    check_call.assert_not_called()


def test_map_by_vendor_id_and_product_id(mocker):
    check_call = mocker.patch("subprocess.check_call")
    add_device_container_mapping(
        "1", {"vendor_id": "aaaa", "product_id": "xxxx"}, "container1"
    )
    share_device_with_container(
        Namespace(
            device="bus/usb/001/099",
            serial_number="9876543210",
            vendor_id="aaaa",
            product_id="xxxx",
        )
    )
    check_call.assert_called_once_with(
        ["lxc-device", "-n", "container1", "add", "/dev/bus/usb/001/099"]
    )


def test_device_missing(mocker):
    mocker.patch("os.path.exists", return_value=False)
    check_call = mocker.patch("subprocess.check_call")
    add_device_container_mapping("1", {"serial_number": "1234567890"}, "mycontainer")
    share_device_with_container(Namespace(device="foo/bar", serial_number="1234567890"))
    check_call.assert_not_called()


def test_share_device_setup_logger(mocker):
    check_call = mocker.patch("subprocess.check_call")
    add_device_container_mapping("1", {"serial_number": "1234567890"}, "mycontainer")
    setup_logger = mocker.Mock()
    share_device_with_container(
        Namespace(device="foo", serial_number="1234567890"), setup_logger
    )
    setup_logger.assert_called_once_with(mocker.ANY)


def test_unknown_container_type(mocker):
    add_device_container_mapping(
        "1",
        {"serial_number": "1234567890"},
        "mycontainer",
        container_type="unsupported",
    )
    with pytest.raises(InfrastructureError):
        share_device_with_container(
            Namespace(device="foo/bar", serial_number="1234567890")
        )
