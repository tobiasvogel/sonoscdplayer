simple: apds9306.c
	gcc -O2 -Wall -Wextra apds9306.c -o apds9306

clean:
	rm -f ./apds9306
	cd service && $(MAKE) clean

.PHONY: service

service:
	$(MAKE) -C service
