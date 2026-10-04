def test_demo_and_admin_pages_served(make_client):
    c = make_client()
    demo = c.get("/demo")
    assert demo.status_code == 200 and 'id="gate"' in demo.text and "assets/demo.js" in demo.text
    assert c.get("/assets/demo.css").status_code == 200
    assert c.get("/assets/demo.js").status_code == 200
    assert c.get("/assets/icons.json").json()["generic-tool"]
