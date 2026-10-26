service: apds9306.c apds9306d.c
	gcc -O2 -Wall -Wextra apds9306d.c -o apds9306d
	gcc -O2 -Wall -Wextra apds9306.c -o apds9306

clean:
	rm -f ./apds9306
	rm -f ./apds9306d

install: service
	install -m 755 apds9306d $(DESTDIR)/usr/local/bin
	install -m 755 apds9306 $(DESTDIR)/usr/local/bin
	install -m 644 apds9306d.service $(DESTDIR)/etc/systemd/system/
	systemctl daemon-reload
	systemctl enable --now apds9306d
