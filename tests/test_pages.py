import re

def test_demo_and_admin_pages_served(make_client):
    c = make_client()
    demo = c.get("/demo")
    assert demo.status_code == 200 and 'id="gate"' in demo.text and "assets/demo.js" in demo.text
    assert c.get("/assets/demo.css").status_code == 200
    assert c.get("/assets/demo.js").status_code == 200
    assert c.get("/assets/icons.json").json()["generic-tool"]


def test_admin_page_and_prototype_link(make_client):
    c = make_client()
    admin = c.get("/admin")
    assert admin.status_code == 200 and "assets/admin.js" in admin.text
    proto = c.get("/prototype.html").text
    assert 'href="/demo"' in proto and "Prototype Coming Soon" not in proto
    assert "data-access-form" in proto


def test_site_links_to_demo(make_client):
    c = make_client()
    home = c.get("/").text
    assert 'href="/demo"' in home and "See Onesi in action" in home
    assert "<video" in home and "demo-video.mp4" in home
    for page in ("/", "/prototype.html", "/team.html", "/contact.html"):
        html = c.get(page).text
        assert not re.search(r'href="contact\.html"[^>]*>Request a Demo', html)
    assert "data-access-form" in c.get("/contact.html").text
