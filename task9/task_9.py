import os
import json
import logging
import requests
from parsel import Selector
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

git_login = os.getenv("GIT_LOGIN")
git_password = os.getenv("GIT_PASSWORD")
user = os.getenv("GIT_USER")

url = "https://github.com/login"
notifs_url = "https://github.com/notifications"
repos_url = "https://github.com/repos"
stars_url = f"https://github.com/{user}?tab=stars"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

class GitHubAuth:

    def get_cookies(self):
        with sync_playwright() as p:
            browser =  p.chromium.launch(headless=False)
            context =  browser.new_context()
            page =  context.new_page()

            page.goto(url)

            page.fill("#login_field", git_login)

            page.fill("#password", git_password)

            with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                page.get_by_role("button", name="Sign in").first.click()

            cookies =  page.context.cookies()

            session = requests.Session()

            for cookie in cookies:
                session.cookies.set(
                    cookie["name"],
                    cookie["value"],
                    domain=cookie["domain"],
                    path=cookie["path"],
                )

            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://github.com/",
            })
            browser.close()
        return session

    def get_notifications(self, session):
        r = session.get(notifs_url)
        html=r.text

        selector = Selector(text=html)

        all_caught_up = selector.css("h2.blankslate-heading::text").get()

        if all_caught_up and "All caught up!" in all_caught_up:
            notifs = "No notifications"

        return notifs

    def get_repos(self, session):
        r = session.get(repos_url)
        html = r.text
        sel = Selector(text=html)
        repos = []
        data = sel.css('script[data-target="react-app.embeddedData"]::text').get()

        if not data:
            print("no")
            return repos

        data = json.loads(data)

        repositories = data["payload"]["reposFinderPageRoute"]["repositories"]

        for repo in repositories:
            repos.append(repo["name"])

        return repos

    def get_starred_repos(self, session):
        r = session.get(stars_url)
        sel = Selector(text=r.text)

        blankslate = sel.css('div.blankslate h3::text').get()
        if blankslate:
            return "No starred repositories"

        repos = []
        for li in sel.css('li.ListItem-module__listItem__wBJcm'):
            owner = li.css('.ReposListItem-module__RepoOwner__Xcxb6::text').get()
            name_parts = li.css('.ReposListItem-module__NwoTitle__l7gRA a::text').getall()
            name = name_parts[-1].strip() if name_parts else None
            url = li.css('a.Title-module__anchor__dBbYy::attr(href)').get()
            if url:
                url = "https://github.com" + url
            language = li.css('.ReposListItem-module__PrimaryLanguageName__Khb4P::text').get()

            repos.append({
                "owner": owner.strip() if owner else None,
                "name": name,
                "url": url,
                "language": language.strip() if language else None
            })

        return repos

    def fetch_all(self, notifs, repos, starred_repos):
        logger.info("Notifications:  %s", notifs)
        for repo in repos:
            logger.info("Repo: %s", repo)
        logger.info("Starred Repos: %s", starred_repos)


    def main(self):
        session = self.get_cookies()
        notifs = self.get_notifications(session)
        repos = self.get_repos(session)
        starred_repos = self.get_starred_repos(session)

        self.fetch_all(notifs, repos, starred_repos)

if __name__ == "__main__":
    github_auth = GitHubAuth()
    github_auth.main()