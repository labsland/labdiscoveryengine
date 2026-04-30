from flask_assets import Bundle, Environment

BOOTSTRAP_VERSION = '5.3.1'

CSS_FILTER = 'cssmin'
JS_FILTER = 'jsmin'

def _register_bundle(assets: Environment, name: str, bundle: Bundle):
    if name in assets:
        assets._named_bundles.pop(name, None)
    assets.register(name, bundle)


def register_bundles(assets: Environment):
    """
    Registers the bundles for the assets.
    :param assets: The assets environment.
    """
    bootstrap_css = Bundle(
            'node_modules/bootstrap/dist/css/bootstrap.min.css',
            filters=CSS_FILTER,
            output="gen/bootstrap-" + BOOTSTRAP_VERSION + "/css/bootstrap.%(version)s.min.css")

    bootstrap_js = Bundle(
            'node_modules/bootstrap/dist/js/bootstrap.min.js',
            filters=JS_FILTER,
            output="gen/bootstrap-" + BOOTSTRAP_VERSION + "/js/bootstrap.%(version)s.min.js")

    _register_bundle(assets, 'bootstrap_css', bootstrap_css)
    _register_bundle(assets, 'bootstrap_js', bootstrap_js)


    fontawesome_css = Bundle(
            'node_modules/@fortawesome/fontawesome-free/css/all.min.css',
            filters=CSS_FILTER,
            output="gen/fontawesome/css/all.%(version)s.min.css")

    fontawesome_js = Bundle(
            'node_modules/@fortawesome/fontawesome-free/js/all.min.js',
            filters=JS_FILTER,
            output="gen/fontawesome/js/all.%(version)s.min.js")

    _register_bundle(assets, 'fontawesome_css', fontawesome_css)
    _register_bundle(assets, 'fontawesome_js', fontawesome_js)

    vendor_js = Bundle(
            "node_modules/jquery/dist/jquery.min.js",
            filters=JS_FILTER,
            output="gen/vendor.%(version)s.min.js")

    _register_bundle(assets, 'vendor_js', vendor_js)
