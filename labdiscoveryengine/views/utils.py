from flask import render_template, current_app

def render_themed_template(template_name, **kwargs):
    """
    Render a template with the current theme.
    """
    theme = current_app.config['THEME']
    return render_template(f'{theme}/{template_name}', **kwargs)

