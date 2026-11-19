"""Where the package, its assets and the repository checkout are on disk."""
from pathlib import Path

#: src/sonoscdplayer
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
#: src/ – the working directory of the services; python -m starts from here.
SOURCE_ROOT = PACKAGE_ROOT.parent
#: The repository checkout (VERSION, config/, tools/).
REPOSITORY_ROOT = SOURCE_ROOT.parent
#: Images and fonts shipped with the player.
ASSETS = PACKAGE_ROOT / 'assets'
SCREEN_IMAGES = ASSETS / 'screens'
ICONS = ASSETS / 'icons'
FONTS = ASSETS / 'fonts'
