"""Private staff panel; uses the same subscriber keys as the bot."""
from pathlib import Path
import argparse
import os
import sys
sys.path.insert(0,str(Path(__file__).parents[1]/'src'))
from theatre_bot.admin_panel import run_panel
from theatre_bot.subscribers import IdentityProtector

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8090)
    args=parser.parse_args()
    root=Path(__file__).parents[1]
    run_panel(root/'data/subscribers.sqlite3',root/'data/theatre.sqlite3',IdentityProtector.from_environment(),
              args.port,campaigns_enabled=os.environ.get('THEATRE_CAMPAIGNS_ENABLED')=='1')
