# coding: utf-8
import glob
from optparse import make_option
import os

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db.migrations.loader import MigrationLoader
from django.utils.importlib import import_module
from django.core.management.commands.makemigrations import Command as MakeMigrationsCommand


class Command(MakeMigrationsCommand):
    option_list = BaseCommand.option_list

    def handle(self, *app_labels, **options):
        self.verbosity = int(options.get('verbosity'))
        self.interactive = options.get('interactive')
        self.dry_run = False

        self.loader = MigrationLoader(None, ignore_no_migrations=True)

        for app_label in app_labels:
            self.handle_app(app_label)

    def handle_app(self, app_label):
        app = apps.get_app_config(app_label)
        self.convert(app)

    def convert(self, app):
        print('Processing %s' % app.label)

        if not self.has_south_migrations(app) and not self.has_initial_data_outside_of_migrations(app):
            print('App %s is already migrated to new style migrations' % app.label)
            return True

        if self.has_south_migrations(app):
            self.remove_migrations(app)

        self.create_new_migrations(app)

        if self.has_initial_data_outside_of_migrations(app):
            print('Found initial_data outside of migrations')
            self.create_data_migration_from_initial_data(app)

    def has_south_migrations(self, app):
        """ Apps with South migrations are in both sets"""
        return (app.label in self.loader.unmigrated_apps and
                app.label in self.loader.migrated_apps)

    def has_initial_data_outside_of_migrations(self, app):
        # Are there any initial_data fixtures
        if not self.get_initial_data_fixtures(app):
            return False

        # Check if initial data is already inside migration
        leaf_nodes = self.loader.graph.leaf_nodes(app.label)
        if not leaf_nodes:
            return True
        _, migration_name = leaf_nodes[0]
        migration_string = open(os.path.join(self.get_migrations_dir(app), migration_name + '.py')).read()
        if "call_command('loaddata'" in migration_string:
            return False

        return True

    def get_migrations_dir(self, app):
        module_name = self.loader.migrations_module(app.label)
        module = import_module(module_name)
        return os.path.dirname(module.__file__)

    def get_initial_data_fixtures(self, app):
        fixture_dir = os.path.join(app.path, 'fixtures')
        return list(glob.iglob(os.path.join(fixture_dir, 'initial_data.*')))

    def remove_migrations(self, app):
        print('    Removing old South migrations')
        directory = self.get_migrations_dir(app)
        for name in os.listdir(directory):
            if (name.endswith('.py') or name.endswith('.pyc'))and name != '__init__.py':
                print('      Deleting %s %s' % (name, '(fake)' if self.dry_run else ''))
                if not self.dry_run:
                    os.remove(os.path.join(directory, name))

    def create_new_migrations(self, app):
        call_command('makemigrations', app.label, dry_run=self.dry_run, verbosity=self.verbosity)

    def create_data_migration_from_initial_data(self, app):
        # Create empty migration
        call_command('makemigrations', app.label, empty=True, dry_run=self.dry_run, verbosity=self.verbosity)

        # Get latest migration
        self.loader.build_graph()
        _, migration_name = self.loader.graph.leaf_nodes(app.label)[0]

        # Find the file
        directory = self.get_migrations_dir(app)
        empty_migration_file = os.path.join(directory, migration_name + '.py')

        # Inject code
        migration_string = open(empty_migration_file).read()
        callable_code = RUN_CODE % (
            ', '.join(map(lambda fixture_name: '"%s"' % os.path.basename(fixture_name),
                          self.get_initial_data_fixtures(app))),
            app.label
        )
        migration_string = (migration_string.replace('class Migration', callable_code + 'class Migration')
                                            .replace('operations = [', 'operations = [' + OPERATIONS))
        with open(empty_migration_file, "wb") as fh:
            fh.write(migration_string.encode('utf-8'))

        # wipe *.pyc
        try:
            os.remove(os.path.join(directory,  os.path.join(directory, migration_name + '.pyc')))
        except OSError:
            pass


RUN_CODE = """from django.core.management import call_command


def load_fixtures(*args, **kwargs):
    call_command('loaddata', %s, app_label='%s')


"""

OPERATIONS = """
        migrations.RunPython(load_fixtures),"""
