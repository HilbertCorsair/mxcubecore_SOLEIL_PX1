#


# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html


import datetime
import importlib.metadata


PROJECT_PACKAGE_NAME = "mxcubecore"
PROJECT_PACKAGE_METADATA = importlib.metadata.metadata(PROJECT_PACKAGE_NAME)


# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = "MXCuBE-Core"
author = PROJECT_PACKAGE_METADATA["Author"]
copyright = (f"{datetime.datetime.today().year}, {author}",)

version = PROJECT_PACKAGE_METADATA["Version"]
release = version

DOCUMENT_DESCRIPTION = f"{project} documentation"


# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "myst_parser",
]

root_doc = "contents"

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}


# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "alabaster"

html_theme_options = {
    "description": DOCUMENT_DESCRIPTION,
    "github_banner": "true",
    "github_button": "true",
    "github_repo": "mxcubecore",
    "github_user": "mxcube",
}


# -- Extensions --------------------------------------------------------------


# -- Options for sphinx.ext.autodoc
# https://www.sphinx-doc.org/en/master/usage/extensions/autodoc.html

extensions.append("sphinx.ext.autodoc")

autodoc_default_options = {
    "inherited-members": True,
    "members": True,
    "show-inheritance": True,
}

autodoc_typehints = "both"


# -- Options for sphinx.ext.autosummary
# https://www.sphinx-doc.org/en/master/usage/extensions/autosummary.html

extensions.append("sphinx.ext.autosummary")

autosummary_generate = True


# -- Options for sphinx.ext.intersphinx
# https://www.sphinx-doc.org/en/master/usage/extensions/intersphinx.html

extensions.append("sphinx.ext.intersphinx")

intersphinx_mapping = {
    "python": ("https://docs.python.org/3/", None),
}


# -- Options for sphinx.ext.napoleon
# https://www.sphinx-doc.org/en/master/usage/extensions/napoleon.html

# We use Google style docstrings
# https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings

extensions.append("sphinx.ext.napoleon")

napoleon_numpy_docstring = False


# -- Options for sphinx.ext.viewcode
# https://www.sphinx-doc.org/en/master/usage/extensions/viewcode.html

extensions.append("sphinx.ext.viewcode")


# EOF