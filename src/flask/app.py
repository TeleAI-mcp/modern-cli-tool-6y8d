"""
    flask.app
    ~~~~~~~~~

    This module implements the central WSGI application object.

    :copyright: 2010 Pallets
    :license: BSD-3-Clause
"""
import os
import sys
import typing as t
from datetime import timedelta
from functools import partial
from functools import update_wrapper

from werkzeug.exceptions import abort as werkzeug_abort
from werkzeug.exceptions import HTTPException as WerkzeugHTTPException
from werkzeug.exceptions import InternalServerError
from werkzeug.routing import BuildError
from werkzeug.routing import MapAdapter
from werkzeug.routing import RequestRedirect
from werkzeug.serving import make_server
from werkzeug.wrappers import Request as WerkzeugRequest

from . import cli
from . import json
from .config import Config
from .ctx import AppContext
from .ctx import RequestContext
from .globals import _app_ctx_stack
from .globals import _request_ctx_stack
from .helpers import _endpoint_from_view_func
from .helpers import find_package
from .helpers import get_env
from .helpers import get_flashed_messages
from .helpers import get_load_dotenv
from .helpers import url_for
from .sessions import SecureCookieSessionInterface
from .signals import appcontext_tearing_down
from .signals import appcontext_pushed
from .signals import appcontext_popped
from .signals import before_render_template
from .signals import request_started
from .signals import request_finished
from .signals import request_tearing_down
from .signals import got_request_exception
from .signals import template_rendered
from .templating import DispatchingJinjaLoader
from .templating import Environment
from .typing import AfterRequestCallable
from .typing import AppOrBlueprintKey
from .typing import BeforeFirstRequestCallable
from .typing import BeforeRequestCallable
from .typing import ErrorHandlerCallable
from .typing import TeardownCallable
from .typing import TemplateContextProcessorCallable
from .typing import TemplateFilterCallable
from .typing import TemplateGlobalCallable
from .typing import TemplateTestCallable
from .typing import URLDefaultCallable
from .typing import URLValuePreprocessorCallable
from .wrappers import Request
from .wrappers import Response

if t.TYPE_CHECKING:
    from .blueprints import Blueprint
    from .testing import FlaskClient

F = t.TypeVar("F", bound=t.Callable[..., t.Any])
T_route = t.TypeVar("T_route", bound=t.Callable[..., t.Any])
T_error_handler = t.TypeVar("T_error_handler", bound=t.Callable[..., t.Any])
T_shell_context_processor = t.TypeVar(
    "T_shell_context_processor", bound=t.Callable[..., t.Dict[str, t.Any]]
)


def _make_timedelta(value: t.Union[timedelta, int, float, None]) -> timedelta:
    if value is None or isinstance(value, timedelta):
        return value  # type: ignore
    return timedelta(seconds=int(value))


class Flask:
    """The flask object implements a WSGI application and acts as the central
    object.  It is passed the name of the module or package of the
    application.  Once it is created it will act as a central registry for
    the view functions, the URL rules, template configuration and much more.

    The name of the package is used to resolve resources from inside the
    package or the folder the module is contained in depending on if the
    package parameter resolves to an actual python package (a folder with
    an :file:`__init__.py` file inside) or a standard module (just a ``.py`` file).

    For more information about resource loading, see :func:`open_resource`.

    Usually you create a :class:`Flask` instance in your main module or
    in the :file:`__init__.py` file of your package like this::

        from flask import Flask
        app = Flask(__name__)

    .. admonition:: About the First Parameter

        The idea of the first parameter is to give Flask an idea of what
        belongs to your application.  This name is used to find resources
        on the filesystem, can be used by extensions to improve debugging
        information and a lot more.

        So it's important what you provide there.  If you are using a single
        module, `__name__` is always the correct value.  If you however are
        using a package, it's usually recommended to hardcode the name of
        your package there.

        For example if your application is defined in :file:`yourapplication/app.py`
        you should create it with one of the two versions below::

            app = Flask('yourapplication')
            app = Flask(__name__.split('.')[0])

        Why is that?  The application will work even with `__name__`, thanks
        to how resources are looked up.  However it will make debugging more
        painful.  Certain extensions can make assumptions based on the
        import name of your application.

        For example the Flask-SQLAlchemy extension will look for the code in
        your application that triggered an SQL query in debug mode.  If the
        import name is not set up properly, that debugging information is
        lost.  (For example it would only pick up the SQL queries in
        `yourapplication.app` and not `yourapplication.views.frontend`)

    .. versionadded:: 0.7
       The ``static_url_path``, ``static_folder``, and ``template_folder``
       parameters were added.

    .. versionadded:: 0.8
       The ``instance_path`` and ``instance_relative_config`` parameters were
       added.

    .. versionadded:: 1.0
       The ``host_matching`` and ``static_host`` parameters were added.

    .. versionadded:: 1.0
       The ``subdomain_matching`` parameter was added. Subdomain
       matching needs to be enabled in order to set the ``subdomain``
       parameter for routes.

    .. versionadded:: 1.0
       The ``propagate_exceptions`` parameter was added.

    .. versionadded:: 1.0
       The ``trap_http_exceptions`` parameter was added.

    .. versionadded:: 1.0
       The ``extra_files`` parameter was added.

    .. versionadded:: 1.0
       The ``import_name`` parameter is now required.

    .. versionadded:: 1.0
       The ``root_path`` parameter was added.

    .. versionadded:: 1.1
       The ``json_encoder`` and ``json_decoder`` parameters were added.

    .. versionadded:: 1.1
       The ``shell_context_processor`` decorator was added.

    .. versionadded:: 1.2
       The ``template_folder`` parameter can be a list of paths.

    .. versionadded:: 2.0
       The ``env`` parameter was added.

    .. versionadded:: 2.0
       The ``view_functions`` attribute was added.

    .. versionadded:: 2.2
       The ``static_folder`` parameter can be a list of paths.

    .. versionchanged:: 2.2
       Added the :attr:`app_context` attribute.

    .. versionchanged:: 2.2
       Added the :attr:`test_cli_runner` attribute.

    .. versionchanged:: 2.3
       Added the :attr:`cli` attribute.

    .. versionchanged:: 2.3
       Added the :attr:`cli_class` attribute.

    .. versionchanged:: 2.3
       Added the :attr:`cli_runner_class` attribute.
    """

    #: The class that is used for the ``test_client`` property.  See
    #: :class:`~flask.testing.FlaskClient` for more information.
    #:
    #: .. versionadded:: 2.3
    test_client_class: t.Optional[t.Type["FlaskClient"]] = None

    #: The class that is used for the ``test_cli_runner`` property.  See
    #: :class:`~flask.testing.FlaskCliRunner` for more information.
    #:
    #: .. versionadded:: 2.3
    test_cli_runner_class: t.Optional[t.Type["cli.FlaskCliRunner"]] = None

    #: The class that is used for the :attr:`cli` attribute.  See
    #: :class:`~flask.cli.FlaskGroup` for more information.
    #:
    #: .. versionadded:: 2.3
    cli_class: t.Type[cli.FlaskGroup] = cli.FlaskGroup

    def __init__(
        self,
        import_name: str,
        static_url_path: t.Optional[str] = None,
        static_folder: t.Optional[t.Union[str, os.PathLike, t.List[t.Union[str, os.PathLike]]]] = None,
        static_host: t.Optional[str] = None,
        host_matching: bool = False,
        subdomain_matching: bool = False,
        template_folder: t.Optional[t.Union[str, t.List[str]]] = "templates",
        instance_path: t.Optional[str] = None,
        instance_relative_config: bool = False,
        root_path: t.Optional[str] = None,
    ) -> None:
        self.import_name = import_name

        self.static_folder = static_folder
        self.static_url_path = static_url_path
        self.static_host = static_host

        if template_folder is not None:
            if isinstance(template_folder, str):
                template_folder = [template_folder]
            self.template_folder = template_folder
        else:
            self.template_folder = None

        self.instance_path = instance_path
        self.instance_relative_config = instance_relative_config

        self.root_path = root_path

        self.config = self.make_config()

        self.view_functions: t.Dict[str, t.Callable] = {}
        self.error_handler_spec: t.Dict[
            AppOrBlueprintKey,
            t.Dict[t.Optional[int], t.Dict[t.Type[Exception], t.Callable]],
        ] = {}
        self.before_request_funcs: t.Dict[
            AppOrBlueprintKey, t.List[BeforeRequestCallable]
        ] = {}
        self.after_request_funcs: t.Dict[
            AppOrBlueprintKey, t.List[AfterRequestCallable]
        ] = {}
        self.before_first_request_funcs: t.List[BeforeFirstRequestCallable] = []
        self.teardown_request_funcs: t.Dict[
            AppOrBlueprintKey, t.List[TeardownCallable]
        ] = {}
        self.teardown_appcontext_funcs: t.List[TeardownCallable] = []
        self.url_default_functions: t.Dict[
            AppOrBlueprintKey, t.List[URLDefaultCallable]
        ] = {}
        self.url_value_preprocessors: t.Dict[
            AppOrBlueprintKey, t.List[URLValuePreprocessorCallable]
        ] = {}
        self.template_context_processors: t.Dict[
            AppOrBlueprintKey, t.List[TemplateContextProcessorCallable]
        ] = {}
        self.shell_context_processors: t.List[T_shell_context_processor] = []
        self.url_map_class = MapAdapter
        self.url_map = self.url_map_class()
        self.blueprints: t.Dict[str, "Blueprint"] = {}
        self.extensions: t.Dict[str, t.Any] = {}
        self._got_first_request = False
        self._before_request_lock = t.RLock()

        self.cli = self.cli_class(self)

        self.app_context = AppContext(self)

    @property
    def name(self) -> str:
        """The name of the application.  This is usually the import name
        with the difference that it's guessed from the run file if the
        import name is "__main__".
        """
        if self.import_name == "__main__":
            fn: t.Optional[str] = getattr(sys.modules["__main__"], "__file__", None)
            if fn is None:
                return "__main__"
            return os.path.splitext(os.path.basename(fn))[0]
        return self.import_name

    def make_config(self) -> Config:
        """Used to create the config attribute by the Flask constructor.
        The `instance_relative_config` is set to `True` if the application
        was created with the ``instance_relative_config`` parameter set to
        `True`.

        Can be overridden to customize the config class.

        .. versionadded:: 0.8
        """
        return Config(self.root_path, self)

    def run(
        self,
        host: t.Optional[str] = None,
        port: t.Optional[int] = None,
        debug: t.Optional[bool] = None,
        load_dotenv: bool = True,
        **options: t.Any,
    ) -> None:
        """Runs the application on a local development server.

        Do not use ``run`` in a production setting. It is not intended to
        meet security and performance requirements for a production server.
        Instead, see :doc:`/deploying` for WSGI server recommendations.

        If the :attr:`debug` flag is set the server will automatically reload
        for code changes and show a debugger in case an exception happens.

        This functionality is disabled if FLASK_DEBUG is disabled.  In any case
        the debugger is only active if the application is running with debug
        mode.

        .. versionchanged:: 1.0
            If port is not specified, use 5000.

        .. versionchanged:: 1.0
            Added ``load_dotenv`` parameter.

        .. versionchanged:: 2.0
            Added ``--cert`` and ``--key`` options to specify a certificate.

        .. versionchanged:: 2.0
            Added support for ``--extra-files``.

        .. versionchanged:: 2.2
            Added support for ``--reload-interval``.

        .. versionchanged:: 2.3
            Added support for ``--reload-patience``.
        """
        from werkzeug.serving import run_simple

        if os.environ.get("FLASK_RUN_FROM_CLI") == "true":
            from .cli import DispatchingApp

            cli.show_server_banner(self)
            app = DispatchingApp(self, load_dotenv=load_dotenv)
        else:
            app = self

        if host is None:
            host = "127.0.0.1"

        if port is None:
            server_name = self.config["SERVER_NAME"]

            if server_name and ":" in server_name:
                port = int(server_name.rsplit(":", 1)[1])
            else:
                port = 5000

        if debug is None:
            debug = self.config["DEBUG"]

        options.setdefault("use_reloader", debug)
        options.setdefault("use_debugger", debug)
        options.setdefault("threaded", True)

        run_simple(host, port, app, **options)
