#!/usr/bin/python3
# -*- coding: utf-8 -*-



import os
import sys
import re
from base64 import b64encode
import subprocess as sp
import urllib3

from ply import lex, yacc




# Retain a copy so we don't unneccessarily waste time recreating them
http          = urllib3.PoolManager()

global_lexer  = None
global_parser = None
tokens        = None




def process_file( file_path, script_vars={}, compress=False, escape=None ):
    '''This is essentially a router for matching file extensions with the
    appropriate processor. If no appropriate processor is available
    default_processor is used. Since some content may be embedded in a string it
    may be necessary to escape the quote characters for that string
    implementation'''

    # get the file extention
    ext = file_path.rsplit( '.', 1 )[-1]

    handler = {
        'htm':  html_processor,
        'html': html_processor,
        'svg':  svg_processor,
        'sass': sass_processor,
        'css':  css_processor,
        'js':   js_processor
    }.get( ext, default_processor )

    content = handler( file_path, script_vars, compress )

    # if the content is used in a string then we'll need to ensure the correct
    # string syntax is escaped.
    # TODO: consider performing a full escape
    if escape:
        content = content.replace( '\\', '\\\\' ).replace( escape, "\\{0}".format( escape ) )

    return content




def import_file( filepath ):
    base64 = False
    # global http

    # if filepath.startswith( 'http://' )
    #     respone = http.request( 'GET', filepath )
    #     print( respone.status, file=sys.stderr )

    #     assert False

    # TODO: proto = file:// for files from root


    if filepath.startswith('base64:'):
        base64   = True
        filepath = filepath[7:]


    if filepath.startswith( '/' ):
        filepath = filepath[1:]
    else:
        filepath = '{0}/{1}'.format( p.lexer.dir_path, filepath )

    data = process_file( filepath, compress=True, escape="'" )

    if base64:
        data = b64encode(data.encode()).decode('utf-8')

    return data



#TODO: catch url contents
def include_file( filepath, dirpath, script_vars ):
    global http

    # TODO: proto = file:// for files from root
    proto = r'^http(s)?://'

    if re.match( proto, filepath ):
        response = http.request( 'GET', filepath )

        if response.status != 200:
            msg = 'Error while including: {0}, status: {1}'.format( filepath, response.status )
            print( msg, file=sys.stderr )
            exit( 1 )

        content_type = response.headers['content-type']
        content_type = content_type.split( '; ' )

        charset = 'utf-8'

        for ct in content_type:
            match = re.match( r'^charset=(.*?)$', ct )

            if match:
                charset = match.group( 1 )
                break

        return response.data.decode( charset )



    if filepath.startswith( '/' ):
        path = filepath[1:]
    else:
        path = '{0}/{1}'.format( dirpath, filepath )

    return js_processor( path, script_vars )





################################################################################
# Lexer ########################################################################
################################################################################




def create_lexer( file_path, script_vars ):
    '''This will generate the lexer for the processor. Since this can be time
    consuming and may be called for each individual file ( to which there may be
    many ), then we'll store a lexer globally and clone it each time we need a
    new one'''

    global global_lexer
    global tokens

    dir_path = os.path.dirname( file_path )

    if global_lexer:
        lexer = global_lexer.clone()
        lexer.file_path   = file_path   # used when displaying systax errors
        lexer.dir_path    = dir_path    # used for finding files via a relative path
        lexer.script_vars = script_vars # we store the variables here as they're generated

        return lexer


    reserved = {
        'begin_scope': 'SCOPE_BEGIN',
        'end_scope':   'SCOPE_END',
        'use_strict':  'USE_STRICT',
        'include':     'INCLUDE',
        'echo':        'ECHO',
    }


    # A list of js reserved ids that we'll recognise in buildr
    js_reserved = {
        'window':    'STRING',
        'undefined': 'STRING'
    }



    tokens = [
        'BLOCK',
        'DELIMITER',
        'NEW_LINE',
        'COMMENT',
        'SEPARATOR',
        # 'LPAREN',
        # 'RPAREN',
        'STRING',
        'ID' ] + list( reserved.values() )


    t_DELIMITER = r';'
    t_SEPARATOR = r','
    t_COMMENT   = r'\#.*?\n'
    # t_LPAREN    = r'\('
    # t_RPAREN    = r'\)'



    def t_NEW_LINE( t ):
        r'\n'

        t.lexer.lineno += 1
        return t


    # we use this to represent a block of text
    def t_BLOCK( t ):
        r'%%BLOCK_BEGIN%%(\n|.)*?%%BLOCK_END%%'

        # we're only interested in the content of the block
        # TODO: move to parser
        block = r'%%BLOCK_BEGIN%%((\n|.)*?)%%BLOCK_END%%'
        t.value = re.match( block, t.value ).group( 1 )

        t.lexer.lineno += t.value.count( '\n' )

        return t


    # string token processor
    def t_STRING( t ):
        r'"(\\"|[^"])*?"'

        # remove the quotes and unescape the text
        # TODO: move to parser
        str = t.value[1:-1]
        t.value = str.encode().decode( 'unicode-escape' )

        return t


    def t_ID( t ):
        r'[a-zA-Z_][a-zA-Z0-9_]*'

        # cast the id to a reserved token if its found in the dict
        t.type = reserved.get( t.value, 'ID' )

        if t.type != 'ID':
            return t

        # cast the id to a js_reserved token if its found in the dict
        t.type = js_reserved.get( t.value, 'ID' )
        return t


    t_ignore = '[ \t]' # TODO: add comment?


    #error processor
    def t_error( t ):
        print( 'Unknown text "{0}"'.format( t.value[0] ), file=sys.stderr )


    global_lexer = lex.lex()

    lexer = global_lexer.clone()
    lexer.file_path   = file_path   # used when displaying systax errors
    lexer.dir_path    = dir_path    # used for finding files via a relative path
    lexer.script_vars = script_vars # we store the variables here as they're generated

    return lexer




################################################################################
# Parser #######################################################################
################################################################################





def create_parser():
    '''This will generate the parser for the processor. Since this can be time
    consuming and may be called for each individual file ( to which there may be
    many ), then we'll store a parser globally return that every time we need a
    new one'''

    global global_parser
    global tokens

    if global_parser:
        return global_parser

    #TODO: FIX
    # calculate the distance of the lexer position from the previous new line
    def find_column( input, t ):
        last_cr = input.rfind( '\n', 0, t.lexpos )
        if last_cr < 0:
            last_cr = 0

        return t.lexpos - last_cr + 1


    def p_output( p ):
        '''output : output block
                  | output delimiter
                  | output new_line
                  | block'''

        p[0] = p[1] + p[2] if len( p ) > 2 else p[1]


    def p_use_scrict( p ):
        '''block : USE_STRICT delimiter
                 | USE_STRICT new_line
                 | USE_STRICT block'''

        p[0] = '"use strict";\n'


    def p_include_1( p ):
        '''block : INCLUDE string delimiter
                 | INCLUDE string new_line
                 | INCLUDE string block'''

        # TODO: actually include

        data = include_file( p[2], p.lexer.dir_path, p.lexer.script_vars )

        p[0] = '{0}\n{1}'.format( data, p[3] )


    def p_echo( p ):
        '''block : ECHO string delimiter
                 | ECHO string new_line
                 | ECHO string block'''

        string = p[2]

        # TODO: move to p_string ???
        importer = r'\$\{(.*?)\}'

        while True:
            match = re.search( importer, string )
            if not match:
                break

            source, value = match.group( 0 ), match.group( 1 )

            value = import_file( value )

            string = string.replace( source, value, 1 )

        p[0] = '{0}\n{1}'.format( string, p[3] )


    ## Function scopes

    # empty scope params
    def p_begin_scope( p ):
        '''begin_scope : SCOPE_BEGIN delimiter
                       | SCOPE_BEGIN new_line'''

        p[0] = ''


    def p_end_scope( p ):
        '''end_scope : SCOPE_END delimiter
                     | SCOPE_END new_line'''

        p[0] = ''


    #scopes with params
    def p_begin_scope_params_1( p ):
        'begin_scope_params : SCOPE_BEGIN string'

        p[0] = p[2]


    def p_begin_scope_params_2( p ):
        'begin_scope_params : begin_scope_params separator string'

        p[0] = p[1] + ',' + p[3]


    def p_begin_scope_with_params( p ):
        '''begin_scope : begin_scope_params delimiter
                       | begin_scope_params new_line'''

        p[0] = p[1]


    def p_end_scope_params_1( p ):
        'end_scope_params : SCOPE_END string'

        p[0] = p[2]


    def p_end_scope_params_2( p ):
        'end_scope_params : end_scope_params separator string'

        p[0] = p[1] + ',' + p[3]


    def p_end_scope_with_params( p ):
        '''end_scope : end_scope_params delimiter
                     | end_scope_params new_line'''

        p[0] = p[1]


    #clear any excess delimiters
    def p_begin_scope_delimiter( p ):
        '''begin_scope : begin_scope delimiter
                       | begin_scope new_line'''
        p[0] = p[1]


    # join the scopes
    def p_scope( p ):
        'block : begin_scope output end_scope'

        p[0] = ';(function({0}){{\n{1}\n}})({2});\n'.format( p[1], p[2], p[3] )


    def p_separator( p ):
        '''separator : separator new_line
                     | SEPARATOR'''

        p[0] = ','


    def p_string( p ):
        'string : STRING'

        p[0] = p[1]


    def p_block_1( p ):
        'block : BLOCK'

        p[0] = p[1]


    def p_new_line( p ):
        'new_line : NEW_LINE'

        p[0] = ''


    def p_delimiter( p ):
        '''delimiter : DELIMITER
                     | COMMENT
                     | begin_scope end_scope''' # an empty scope is pointless, get rid of it

        p[0] = ''



    def p_error( p ):
        # msg = repr( dir( p ) )
        msg = 'Syntax error: unexpected {0} "{1}" in {2} line {3}'.format(
            p.type, p.value, p.lexer.file_path, p.lineno )
        print( msg, file=sys.stderr )



    # TODO: if ( debug )
    global_parser = yacc.yacc()
    return global_parser




################################################################################
# Default Processor ############################################################
################################################################################




def default_processor( file_path, script_vars={}, compress=False ):
    '''not yet implemented'''
    return ''




################################################################################
# SASS Application #############################################################
################################################################################




def call_sass( params ):
    params = ' '.join( params )

    cmd = 'sass {0}'.format( params )

    proc = sp.Popen( cmd, stdout=sp.PIPE, stdin=sp.PIPE, shell=True )

    output, error = proc.communicate()

    return output.decode( 'utf-8' )




################################################################################
# SASS Processor ###############################################################
################################################################################




def sass_processor( file_path, script_vars={}, compress=False ):
    '''This require ruby and sass to be installed.'''
    params = []

    if compress:
        params.append( '-t compressed' )

    params.append( file_path )

    css = call_sass( params )

    # sass leaves a trailing \n
    if compress:
        css = css.replace( '\n', '' )

    return css




################################################################################
# SVG Processor ################################################################
################################################################################




def svg_processor(file_path, script_vars={}, compress=False):
    ''''''

    try:
        svg = open( file_path, 'r').read()
    except (OSError, IOError) as e:
        print(e, file=sys.stderr)
        exit(1)

    if compress:
        #TODO: work out some better compression if possible
        svg = svg.replace('\n', '').replace('\r', '')

    return svg




################################################################################
# HTML Processor ###############################################################
################################################################################




def html_processor( file_path, script_vars={}, compress=False ):
    '''This requires java installed for compression'''
    # TODO: Do some actual processing ( maybe use bottles preprocessor )

    try:
        source = open( file_path, 'r' ).read()
    except ( OSError, IOError ) as e:
        print( e, file=sys.stderr )
        exit( 1 )

    if compress:
        # we'll use the html compressor in /tools
        cmd = 'java -jar {0}/tools/html-compressor/htmlcompressor-1.5.3.jar'.format( os.path.dirname( __file__ ) )

        proc = sp.Popen( cmd, stdout=sp.PIPE, stdin=sp.PIPE, shell=True )
        proc.stdin.write( source.encode() )

        output, error = proc.communicate()

        source = output.decode( 'utf-8' )

    return source




################################################################################
# CSS Processor ################################################################
################################################################################




def css_processor( file_path, script_vars={}, compress=False ):
    '''Not yet implemented.'''
    return ''




################################################################################
# JS Processor #################################################################
################################################################################




def js_processor( file_path, script_vars={}, compress=False ):
    '''Process a given source file, returning the processed JS. the optional
    vars allow for variables not present in the script to bhe passed in to the
    processor. By default compression is not carried out and single quotes/new
    lines are not escaped. compression should be enabled only at the top level
    and escaping should only be performed where the result will be inserted into
     a string'''
    # todo should strings be completely escaped or not?


    lexer    = create_lexer( file_path, script_vars )
    parser   = create_parser()

    try:
        source = open( file_path, 'r' ).read()
    except ( OSError, IOError ) as e:
        print( e, file=sys.stderr )
        exit( 1 )

    code = enclose_js( source )
    code = parser.parse( code, lexer=lexer )

    if compress:
        cmd = 'java -jar {0}/tools/yui-compressor/yuicompressor-2.4.8.jar --type js'.format( os.path.dirname( __file__ ) )

        proc = sp.Popen( cmd, stdout=sp.PIPE, stdin=sp.PIPE, shell=True )
        proc.stdin.write( code.encode() )

        output, error = proc.communicate()

        code = output.decode( 'utf-8' )

    return code





# TODO: use a tokenising switcher in the lexer
def enclose_js( source ):
    '''In order to aid tokenising we'll wrap the JS code in a JS block and
    remove the preprocessor code from the block by finding it and replacing the
    comment tokens with block tokens. We're effectively inverting the code from
    JS code with buildr blocks to buildr code with JS blocks'''

    # wrap all the code ( useful for when there is no buildr code )
    code = '%%BLOCK_BEGIN%%{0}%%BLOCK_END%%'.format( source )

    block = r'/\*##(.*?)\*/'
    container = '%%BLOCK_END%%{0}%%BLOCK_BEGIN%%'



    # replace all buildr code blocks
    while True:
        match = re.search( block, code, re.DOTALL )
        if not match:
            break

        comment, content = match.group( 0 ), match.group( 1 )
        content = container.format( content )

        code = code.replace( comment, content, 1 )



    line = r'//##(.*?)\n'
    container = '%%BLOCK_END%%{0}\n%%BLOCK_BEGIN%%'

    while True:
        match = re.search( line, code )
        if not match:
            break

        comment, content = match.group( 0 ), match.group( 1 )
        content = container.format( content )

        code = code.replace( comment, content, 1 )



    return code







