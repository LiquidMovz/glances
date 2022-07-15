# -*- coding: utf-8 -*-
#
# This file is part of Glances.
#
# SPDX-FileCopyrightText: 2022 Nicolas Hennion <nicolas@nicolargo.com>
#
# SPDX-License-Identifier: LGPL-3.0-only
#

"""InfluxDB (up to InfluxDB 1.7.x) interface class."""

import sys
from platform import node

from glances.logger import logger
from glances.exports.glances_export import GlancesExport

from influxdb import InfluxDBClient
from influxdb.client import InfluxDBClientError


class Export(GlancesExport):
    """This class manages the InfluxDB export module."""

    def __init__(self, config=None, args=None):
        """Init the InfluxDB export IF."""
        super(Export, self).__init__(config=config, args=args)

        # Mandatory configuration keys (additional to host and port)
        self.user = None
        self.password = None
        self.db = None

        # Optional configuration keys
        self.protocol = 'http'
        self.prefix = None
        self.tags = None
        self.hostname = None

        # Load the InfluxDB configuration file
        self.export_enable = self.load_conf(
            'influxdb', mandatories=['host', 'port', 'user', 'password', 'db'], options=['protocol', 'prefix', 'tags']
        )
        if not self.export_enable:
            exit('Missing INFLUXDB version 1 config')

        # The hostname is always add as a tag
        self.hostname = node().split('.')[0]

        # Init the InfluxDB client
        self.client = self.init()

    def init(self):
        """Init the connection to the InfluxDB server."""
        if not self.export_enable:
            return None

        # Correct issue #1530
        if self.protocol is not None and (self.protocol.lower() == 'https'):
            ssl = True
        else:
            ssl = False

        try:
            db = InfluxDBClient(
                host=self.host,
                port=self.port,
                ssl=ssl,
                verify_ssl=False,
                username=self.user,
                password=self.password,
                database=self.db,
            )
            get_all_db = [i['name'] for i in db.get_list_database()]
        except InfluxDBClientError as e:
            logger.critical("Cannot connect to InfluxDB database '%s' (%s)" % (self.db, e))
            sys.exit(2)

        if self.db in get_all_db:
            logger.info("Stats will be exported to InfluxDB server: {}".format(db._baseurl))
        else:
            logger.critical("InfluxDB database '%s' did not exist. Please create it" % self.db)
            sys.exit(2)

        return db

    def _normalize(self, name, columns, points):
        """Normalize data for the InfluxDB's data model.

        :return: a list of measurements.
        """
        ret = []

        # Build initial dict by crossing columns and point
        data_dict = dict(zip(columns, points))

        # issue1871 - Check if a key exist. If a key exist, the value of
        # the key should be used as a tag to identify the measurement.
        keys_list = [k.split('.')[0] for k in columns if k.endswith('.key')]
        if len(keys_list) == 0:
            keys_list = [None]

        for measurement in keys_list:
            # Manage field
            if measurement is not None:
                fields = {
                    k.replace('{}.'.format(measurement), ''): data_dict[k]
                    for k in data_dict
                    if k.startswith('{}.'.format(measurement))
                }
            else:
                fields = data_dict
            # Transform to InfluxDB data model
            # https://docs.influxdata.com/influxdb/v1.8/write_protocols/line_protocol_reference/
            for k in fields:
                #  Do not export empty (None) value
                if fields[k] is None:
                    continue
                # Convert numerical to float
                try:
                    fields[k] = float(fields[k])
                except (TypeError, ValueError):
                    # Convert others to string
                    try:
                        fields[k] = str(fields[k])
                    except (TypeError, ValueError):
                        pass
            # Manage tags
            tags = self.parse_tags(self.tags)
            if 'key' in fields and fields['key'] in fields:
                # Create a tag from the key
                # Tag should be an string (see InfluxDB data model)
                tags[fields['key']] = str(fields[fields['key']])
                # Remove it from the field list (can not be a field and a tag)
                fields.pop(fields['key'])
            # Add the hostname as a tag
            tags['hostname'] = self.hostname
            # Add the measurement to the list
            ret.append({'measurement': name, 'tags': tags, 'fields': fields})
        return ret

    def export(self, name, columns, points):
        """Write the points to the InfluxDB server."""
        # Manage prefix
        if self.prefix is not None:
            name = self.prefix + '.' + name
        # Write input to the InfluxDB database
        if len(points) == 0:
            logger.debug("Cannot export empty {} stats to InfluxDB".format(name))
        else:
            try:
                self.client.write_points(self._normalize(name, columns, points), time_precision="s")
            except Exception as e:
                # Log level set to debug instead of error (see: issue #1561)
                logger.debug("Cannot export {} stats to InfluxDB ({})".format(name, e))
            else:
                logger.debug("Export {} stats to InfluxDB".format(name))
