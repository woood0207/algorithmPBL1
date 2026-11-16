"""Launch the offline nationwide road, train and bus route planner."""
import sys

sys.dont_write_bytecode=True

from localOSM.server import main

if __name__=='__main__':
    arguments=sys.argv[1:]
    if arguments[:1]==['national']:
        arguments=arguments[1:]
    main(arguments)
