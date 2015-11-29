# Pyrexia

IoT temperature sensor display

## Setup

To get an interactive development environment run:

    lein figwheel

and open your browser at [localhost:3449](http://localhost:3449/).
This will auto compile and send all changes to the browser without the
need to reload. After the compilation process is complete, you will
get a Browser Connected REPL. An easy way to try it is:

    (js/alert "Am I connected?")

and you should see an alert in the browser window.

To clean all compiled files:

    lein clean

To create a production build run:

    lein cljsbuild once min

And open your browser in `resources/public/index.html`. You will not
get live reloading, nor a REPL.

## License

Copyright © 2015 Tom Parker

Distributed under the AGPL version 3

## FAQ

If you're on OS X, ``ES_HOST=`docker-machine ip dev` lein figwheel`` (replace "dev" as appropriate for your docker-machine config) is probably the command you want to run instead...
