# Copyright 2015 Hewlett-Packard Development Company, L.P.
#
# Author: Endre Karlson <endre.karlson@hpe.com>
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import datetime

from oslo_log import log as logging
from oslo_utils import timeutils

from designate.producer import tasks
from designate.storage.impl_sqlalchemy import tables
from designate.tests import fixtures
from designate.tests import TestCase


LOG = logging.getLogger(__name__)


class DeletedZonePurgeTest(TestCase):
    number_of_zones = 20
    batch_size = 5
    time_threshold = 24 * 60 * 60

    def setUp(self):
        super(DeletedZonePurgeTest, self).setUp()
        self.config(
            time_threshold=self.time_threshold,
            batch_size=self.batch_size,
            group="producer_task:zone_purge"
        )
        self.purge_task_fixture = self.useFixture(
            fixtures.ZoneManagerTaskFixture(tasks.DeletedZonePurgeTask)
        )

    def _create_deleted_zone(self, name, mock_deletion_time):
        # Create a zone and set it as deleted
        zone = self.create_zone(name=name)
        self._delete_zone(zone, mock_deletion_time)

    def _fetch_all_zones(self):
        # Fetch all zones including deleted ones.
        query = tables.zones.select()
        return self.central_service.storage.session.execute(query).fetchall()

    def _delete_zone(self, zone, mock_deletion_time):
        # Set a zone as deleted
        zid = zone.id.replace('-', '')
        query = tables.zones.update().where(tables.zones.c.id == zid).values(
                action='NONE',
                deleted=zid,
                deleted_at=mock_deletion_time,
                status='DELETED',
        )

        pxy = self.central_service.storage.session.execute(query)
        self.assertEqual(1, pxy.rowcount)

    def _create_deleted_zones(self):
        # Create a number of deleted zones in the past days.
        now = timeutils.utcnow()
        for index in range(self.number_of_zones):
            age = index * (self.time_threshold // self.number_of_zones * 2) - 1
            delta = datetime.timedelta(seconds=age)
            deletion_time = now - delta
            name = "example%d.org." % index
            self._create_deleted_zone(name, deletion_time)

    def test_purge_zones(self):
        # Create X zones, run producer, check if half of the zones
        # are remaining.
        self.config(quota_zones=self.number_of_zones)
        self._create_deleted_zones()

        for remaining in range(5):
            self.purge_task_fixture.task()

        remaning_zones = self._fetch_all_zones()
        self.assertEqual(len(remaning_zones), self.number_of_zones // 2)


class PeriodicGenerateDelayedNotifyTaskTest(TestCase):
    number_of_zones = 20
    batch_size = 5

    def setUp(self):
        super(PeriodicGenerateDelayedNotifyTaskTest, self).setUp()
        self.config(quota_zones=self.number_of_zones)
        self.config(
            interval=1,
            batch_size=self.batch_size,
            group="producer_task:delayed_notify"
        )
        self.generate_delayed_notify_task_fixture = self.useFixture(
            fixtures.ZoneManagerTaskFixture(
                tasks.PeriodicGenerateDelayedNotifyTask
            )
        )

    def _fetch_all_zones(self):
        # Fetch all zones including deleted ones.
        return self._fetch_zones(tables.zones.select())

    def _fetch_zones(self, query):
        # Fetch zones including deleted ones.
        return self.central_service.storage.session.execute(query).fetchall()

    def _create_zones(self):
        # Create a number of zones; half of them with delayed_notify set.
        for index in range(self.number_of_zones):
            name = "example%d.org." % index
            delayed_notify = (index % 2 == 0)
            self.create_zone(
                name=name,
                delayed_notify=delayed_notify,
            )

    def test_generate_delayed_notify_zones(self):
        # Create zones and set some of them as pending update.
        self._create_zones()

        zones = self._fetch_all_zones()
        self.assertEqual(self.number_of_zones, len(zones))

        for remaining in reversed(range(0,
                                        self.number_of_zones // 2,
                                        self.batch_size)):
            self.generate_delayed_notify_task_fixture.task()

            zones = self._fetch_zones(tables.zones.select().where(
                tables.zones.c.delayed_notify))

            self.assertEqual(
                remaining, len(zones),
                message='Remaining zones: %s' % zones
            )
